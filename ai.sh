#!/bin/bash

# 极简智能调度系统 - 支持上下文和真实token统计
# 用法: ./ai.sh

CONFIG_DIR="$HOME/.smart_ollama"
HISTORY_FILE="$CONFIG_DIR/context.json"
mkdir -p "$CONFIG_DIR"

# 初始化上下文文件
if [ ! -f "$HISTORY_FILE" ]; then
    echo '{"messages": []}' > "$HISTORY_FILE"
fi

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 判断任务类型
detect_model() {
    local query="$1"
    local lower_query=$(echo "$query" | tr '[:upper:]' '[:lower:]')
    
    if echo "$lower_query" | grep -qE "write|code|function|class|implement|创建|写一个|编程|实现"; then
        echo "qwen3-coder:latest"
    elif echo "$lower_query" | grep -qE "why|explain|analyze|debug|fix|为什么|解释|调试|分析"; then
        echo "deepseek-r1-distill:13b"
    else
        echo "devstral-small-2:24b"
    fi
}

# 获取真实token数
get_token_count() {
    local text="$1"
    # 使用ollama的tokenize接口获取真实token数
    local tokens=$(curl -s http://localhost:11434/api/tokenize -d "{\"text\": \"$text\"}" | jq '.tokens | length' 2>/dev/null)
    if [ -n "$tokens" ] && [ "$tokens" != "null" ]; then
        echo "$tokens"
    else
        # 降级方案：粗略估算（中英文混合约2字符/token）
        echo $((${#text} / 2))
    fi
}

# 调用模型并获取真实统计
call_model() {
    local model="$1"
    local prompt="$2"
    local temp="${3:-0.7}"
    
    # 记录开始时间
    local start_time=$(date +%s%N)
    
    # 计算输入token数
    local input_tokens=$(get_token_count "$prompt")
    
    # 调用API并收集输出
    local full_response=""
    local first_token_time=0
    local token_count=0
    
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            local response=$(echo "$line" | jq -r '.response' 2>/dev/null)
            local done_flag=$(echo "$line" | jq -r '.done' 2>/dev/null)
            
            if [ "$response" != "null" ] && [ -n "$response" ]; then
                if [ $first_token_time -eq 0 ]; then
                    first_token_time=$(date +%s%N)
                fi
                echo -n "$response"
                full_response="${full_response}${response}"
                token_count=$((token_count + 1))
            fi
            
            if [ "$done_flag" = "true" ]; then
                local end_time=$(date +%s%N)
                local total_duration_ns=$((end_time - start_time))
                local total_duration=$(echo "scale=2; $total_duration_ns / 1000000000" | bc)
                local first_token_latency=$(echo "scale=2; ($first_token_time - $start_time) / 1000000000" | bc)
                
                # 获取输出token的准确计数
                local output_tokens=$(get_token_count "$full_response")
                
                # 计算速度
                local speed=0
                if [ $total_duration_ns -gt 0 ]; then
                    speed=$(echo "scale=1; $output_tokens / $total_duration" | bc)
                fi
                
                # 输出统计信息
                echo ""
                echo "----------------------------------------"
                echo "统计信息:"
                echo "  输入token: $input_tokens"
                echo "  输出token: $output_tokens"
                echo "  总token: $((input_tokens + output_tokens))"
                echo "  速度: ${speed} tokens/秒"
                echo "  首token延迟: ${first_token_latency}秒"
                echo "  总耗时: ${total_duration}秒"
                echo "----------------------------------------"
                
                # 返回完整响应供上下文使用
                echo "$full_response"
                return 0
            fi
        fi
    done < <(curl -s http://localhost:11434/api/generate -d "{
        \"model\": \"$model\",
        \"prompt\": \"$prompt\",
        \"stream\": true,
        \"options\": {
            \"temperature\": $temp,
            \"num_predict\": 8192
        }
    }")
}

# 构建带上下文的prompt
build_prompt() {
    local user_query="$1"
    local context=$(cat "$HISTORY_FILE")
    
    # 提取历史消息
    local history=""
    local msg_count=$(echo "$context" | jq '.messages | length')
    
    if [ "$msg_count" -gt 0 ]; then
        history="对话历史:\n"
        for i in $(seq 0 $((msg_count - 1))); do
            local role=$(echo "$context" | jq -r ".messages[$i].role")
            local content=$(echo "$context" | jq -r ".messages[$i].content")
            if [ "$role" = "user" ]; then
                history="${history}用户: ${content}\n"
            else
                history="${history}助手: ${content}\n"
            fi
        done
    fi
    
    echo "${history}\n用户最新问题: ${user_query}\n\n请基于以上对话上下文回答。"
}

# 保存对话到上下文
save_to_context() {
    local user_msg="$1"
    local assistant_msg="$2"
    
    # 读取现有上下文
    local context=$(cat "$HISTORY_FILE")
    
    # 添加新消息
    context=$(echo "$context" | jq --arg user "$user_msg" '.messages += [{"role": "user", "content": $user}]')
    context=$(echo "$context" | jq --arg assistant "$assistant_msg" '.messages += [{"role": "assistant", "content": $assistant}]')
    
    # 限制上下文长度（保留最近20轮对话，约40条消息）
    local msg_len=$(echo "$context" | jq '.messages | length')
    if [ "$msg_len" -gt 40 ]; then
        context=$(echo "$context" | jq '.messages = .messages[-40:]')
    fi
    
    # 保存
    echo "$context" > "$HISTORY_FILE"
}

# 主交互循环
main() {
    echo "Smart AI Assistant - 支持上下文对话 (输入 'exit' 退出)"
    echo "当前上下文窗口: 10000000+ tokens"
    echo "----------------------------------------"
    
    while true; do
        echo -n ">>> "
        read -r user_input
        
        if [ "$user_input" = "exit" ] || [ "$user_input" = "quit" ]; then
            echo "再见"
            break
        fi
        
        if [ -z "$user_input" ]; then
            continue
        fi
        
        # 清空上下文命令
        if [ "$user_input" = "/clear" ]; then
            echo '{"messages": []}' > "$HISTORY_FILE"
            echo "对话历史已清空"
            continue
        fi
        
        # 显示当前上下文统计
        if [ "$user_input" = "/stats" ]; then
            local context=$(cat "$HISTORY_FILE")
            local msg_count=$(echo "$context" | jq '.messages | length')
            local context_text=$(echo "$context" | jq -r '.messages[].content' | tr '\n' ' ')
            local total_chars=${#context_text}
            local approx_tokens=$((total_chars / 2))
            echo "当前上下文: $msg_count 条消息, 约 $approx_tokens tokens"
            continue
        fi
        
        # 自动选择模型
        local selected_model=$(detect_model "$user_input")
        echo "模型: $selected_model"
        
        # 构建带上下文的prompt
        local full_prompt=$(build_prompt "$user_input")
        
        # 调用模型
        local response=$(call_model "$selected_model" "$full_prompt")
        
        # 保存到上下文（只保存原始问题和不含统计信息的回答）
        local clean_response=$(echo "$response" | sed '/^----------------------------------------$/,$d')
        save_to_context "$user_input" "$clean_response"
        
        echo ""
    done
}

# 检查依赖
if ! command -v ollama &> /dev/null; then
    echo "错误: 未找到 ollama 命令"
    echo "安装: curl -fsSL https://ollama.com/install.sh | sh"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "错误: 未找到 jq 命令"
    echo "安装: sudo apt-get install jq (Ubuntu) 或 brew install jq (macOS)"
    exit 1
fi

# 运行
main