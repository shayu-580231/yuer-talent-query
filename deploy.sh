#!/bin/bash
# 鱼耳达人查询平台 - 一键部署脚本
# 在腾讯云轻量服务器上运行此脚本即可

set -e

echo "=========================================="
echo "  鱼耳达人查询平台 - 一键部署"
echo "=========================================="
echo ""

# 检查是否是root
if [ "$EUID" -ne 0 ]; then
  echo "请用root用户运行: sudo bash deploy.sh"
  exit 1
fi

# 安装 Docker（如果没有）
if ! command -v docker &> /dev/null; then
  echo "[1/5] 安装 Docker..."
  curl -fsSL https://get.docker.com | bash
  systemctl start docker
  systemctl enable docker
  echo "Docker 安装完成"
else
  echo "[1/5] Docker 已安装，跳过"
fi

# 安装 docker-compose
if ! command -v docker-compose &> /dev/null; then
  echo "[2/5] 安装 docker-compose..."
  curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
  echo "docker-compose 安装完成"
else
  echo "[2/5] docker-compose 已安装，跳过"
fi

# 创建部署目录
DEPLOY_DIR="/opt/yuer-query"
echo "[3/5] 创建部署目录..."
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# 创建 docker-compose.yml
echo "[4/5] 生成配置文件..."
cat > docker-compose.yml << 'EOF'
version: '3'
services:
  yuer-query:
    build: .
    container_name: yuer-query
    restart: always
    ports:
      - "80:7860"
    environment:
      - PYTHONUNBUFFERED=1
    shm_size: '512m'
EOF

# 创建 Dockerfile
cat > Dockerfile << 'DEOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
COPY . .
EXPOSE 7860
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:7860", "app:app"]
DEOF

# 创建 requirements.txt
cat > requirements.txt << 'REQEOF'
flask==3.0.0
requests==2.31.0
playwright==1.40.0
gunicorn==21.2.0
REQEOF

echo "[5/5] 从 GitHub 拉取代码..."

# 从 GitHub 拉取代码文件
GITHUB_RAW="https://raw.githubusercontent.com/shayu-580231/yuer-talent-query/main"

curl -sL "$GITHUB_RAW/app.py" -o app.py
curl -sL "$GITHUB_RAW/templates/index.html" -o templates/index.html 2>/dev/null || {
  mkdir -p templates
  curl -sL "$GITHUB_RAW/templates/index.html" -o templates/index.html
}

echo ""
echo "=========================================="
echo "  开始构建并启动服务..."
echo "=========================================="
docker-compose up -d --build

# 等待启动
echo "等待服务启动..."
sleep 10

# 获取服务器公网IP
PUBLIC_IP=$(curl -s http://metadata.tencentyun.com/latest/meta-data/public-ipv4 2>/dev/null || curl -s ifconfig.me 2>/dev/null || echo "你的服务器IP")

echo ""
echo "=========================================="
echo "  ✅ 部署完成！"
echo "=========================================="
echo ""
echo "  访问地址: http://$PUBLIC_IP"
echo ""
echo "  首次使用: 打开网址 → 输入鱼耳手机号+密码 → 登录"
echo "  所有人都能通过这个地址访问"
echo ""
echo "  管理命令:"
echo "    查看日志: docker logs -f yuer-query"
echo "    重启服务: docker restart yuer-query"
echo "    停止服务: docker stop yuer-query"
echo ""
echo "=========================================="
