#!/bin/bash

# 自动下载 Ollama 并运行 AI 对话系统
# 用法: ./ai.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OLLAMA_DIR="$SCRIPT_DIR/ollama_local"
OLLAMA_BIN="$OLLAMA_DIR/ollama"
MODEL_DIR="$OLLAMA_DIR/models"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 检测系统架构
detect_arch() {
    local arch=$(uname -m)
    case $arch in
        x86_64)
            echo "amd64"
            ;;
        aarch64|arm64)
            echo "arm64"
            ;;
        *)
            echo "unsupported"
            ;;
    esac
}

# 下载 Ollama
download_ollama() {
    local arch=$(detect_arch)
    if [ "$arch" = "unsupported" ]; then
        echo -e "${RED}错误: 不支持的架构 $(uname -m)${NC}"
        exit 1
    fi
    
    local ollama_url="https://github.com/ollama/ollama/releases/download/v0.3.14/ollama-linux-${arch}"
    
    echo -e "${BLUE}下载 Ollama (${arch})...${NC}"
    mkdir -p "$OLLAMA_DIR"
    
    if command -v wget &> /dev/null; then
        wget -O "$OLLAMA_BIN" "$ollama_url"
    elif command -v curl &> /dev/null; then
        curl -L -o "$OLLAMA_BIN" "$ollama_url"
    else
        echo -e "${RED}错误: 需要 wget 或 curl${NC}"
        exit 1
    fi
    
    chmod +x "$OLLAMA_BIN"
    echo -e "${GREEN}Ollama 下载完成: $OLLAMA_BIN${NC}"
}

# 启动 Ollama 服务
start_ollama() {
    echo -e "${BLUE}启动 Ollama 服务...${NC}"
    
    # 检查是否已有进程在运行
    if pgrep -f "$OLLAMA_BIN" > /dev/null; then
        echo -e "${YELLOW}Ollama 已在运行${NC}"
        return 0
    fi
    
    # 设置本地目录
    export OLLAMA_MODELS="$MODEL_DIR"
    
    # 后台启动
    nohup "$OLLAMA_BIN" serve > "$OLLAMA_DIR/ollama.log" 2>&1 &
    
    # 等待服务就绪
    local max_wait=30
    local waited=0
    while ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do
        if [ $waited -ge $max_wait ]; then
            echo -e "${RED}Ollama 启动超时${NC}"
            cat "$OLLAMA_DIR/ollama.log"
            exit 1
        fi
        sleep 1
        waited=$((waited + 1))
        echo -n "."
    done
    echo ""
    echo -e "${GREEN}Ollama 服务已启动${NC}"
}

# 检查并下载模型
ensure_model() {
    local model="$1"
    echo -e "${BLUE}检查模型: $model${NC}"
    
    # 使用本地 ollama 检查
    if ! "$OLLAMA_BIN" list | grep -q "$model"; then
        echo -e "${YELLOW}下载模型 $model (可能需要几分钟)...${NC}"
        "$OLLAMA_BIN" pull "$model"
        echo -e "${GREEN}模型 $model 下载完成${NC}"
    else
        echo -e "${GREEN}模型 $model 已存在${NC}"
    fi
}

# 配置
CONFIG_DIR="$SCRIPT_DIR/ai_context"
HISTORY_FILE="$CONFIG_DIR/context.json"
mkdir -p "$CONFIG_DIR"

if [ ! -f "$HISTORY_FILE" ]; then
    echo '{"messages": []}' > "$HISTORY_FILE"
fi

# 检测任务类型
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
    local tokens=$(curl -s http://localhost:11434/api/tokenize -d "{\"text\": \"$text\"}" 2>/dev/null | jq '.tokens | length' 2>/dev/null)
    if [ -n "$tokens" ] && [ "$tokens" != "null" ]; then
        echo "$tokens"
    else
        echo $((${#text} / 2))
    fi
}

# 调用模型
call_model() {
    local model="$1"
    local prompt="$2"
    local temp="${3:-0.7}"
    
    local start_time=$(date +%s%N)
    local input_tokens=$(get_token_count "$prompt")
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
                local output_tokens=$(get_token_count "$full_response")
                
                local speed=0
                if [ $total_duration_ns -gt 0 ]; then
                    speed=$(echo "scale=1; $output_tokens / $total_duration" | bc)
                fi
                
                echo ""
                echo "----------------------------------------"
                echo "输入token: $input_tokens"
                echo "输出token: $output_tokens"
                echo "总token: $((input_tokens + output_tokens))"
                echo "速度: ${speed} tokens/秒"
                echo "首token延迟: ${first_token_latency}秒"
                echo "总耗时: ${total_duration}秒"
                echo "----------------------------------------"
                
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

# 构建prompt
build_prompt() {
    local user_query="$1"
    local context=$(cat "$HISTORY_FILE")
    
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

# 保存对话
save_to_context() {
    local user_msg="$1"
    local assistant_msg="$2"
    
    local context=$(cat "$HISTORY_FILE")
    context=$(echo "$context" | jq --arg user "$user_msg" '.messages += [{"role": "user", "content": $user}]')
    context=$(echo "$context" | jq --arg assistant "$assistant_msg" '.messages += [{"role": "assistant", "content": $assistant}]')
    
    local msg_len=$(echo "$context" | jq '.messages | length')
    if [ "$msg_len" -gt 40 ]; then
        context=$(echo "$context" | jq '.messages = .messages[-40:]')
    fi
    
    echo "$context" > "$HISTORY_FILE"
}

# 主函数
main() {
    # 检查 Ollama 是否存在
    if [ ! -f "$OLLAMA_BIN" ]; then
        download_ollama
    fi
    
    start_ollama
    
    # 预下载常用模型（可选）
    echo -e "${YELLOW}提示: 首次使用会自动下载模型，可能需要几分钟${NC}"
    
    echo "Smart AI Assistant - 支持上下文对话"
    echo "输入 'exit' 退出, '/clear' 清空历史, '/stats' 查看统计"
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
        
        if [ "$user_input" = "/clear" ]; then
            echo '{"messages": []}' > "$HISTORY_FILE"
            echo "对话历史已清空"
            continue
        fi
        
        if [ "$user_input" = "/stats" ]; then
            local context=$(cat "$HISTORY_FILE")
            local msg_count=$(echo "$context" | jq '.messages | length')
            echo "当前消息数: $msg_count"
            continue
        fi
        
        local selected_model=$(detect_model "$user_input")
        echo "模型: $selected_model"
        
        # 确保模型已下载
        ensure_model "$selected_model"
        
        local full_prompt=$(build_prompt "$user_input")
        local response=$(call_model "$selected_model" "$full_prompt")
        
        local clean_response=$(echo "$response" | sed '/^----------------------------------------$/,$d')
        save_to_context "$user_input" "$clean_response"
        
        echo ""
    done
}

# 清理函数
cleanup() {
    echo ""
    echo -e "${YELLOW}关闭 Ollama 服务...${NC}"
    pkill -f "$OLLAMA_BIN" 2>/dev/null || true
    exit 0
}

trap cleanup INT TERM

# 检查依赖
if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}安装 jq...${NC}"
    if command -v apt &> /dev/null; then
        sudo apt install -y jq
    elif command -v yum &> /dev/null; then
        sudo yum install -y jq
    else
        echo -e "${RED}请手动安装 jq${NC}"
        exit 1
    fi
fi

main