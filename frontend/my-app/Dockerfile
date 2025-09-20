FROM arm64v8/node:20-bookworm-slim

# Update npm to latest version to fix optional dependencies issue
RUN npm install -g npm@latest

WORKDIR /app

# Copy package files
COPY package*.json ./

# Clean install with proper platform targeting and cache optimization
RUN rm -rf node_modules package-lock.json && \
    npm cache clean --force && \
    npm install --legacy-peer-deps --platform=linux --arch=arm64 --no-audit --no-fund

# Explicitly install rollup ARM64 native dependencies
RUN npm install --platform=linux --arch=arm64 @rollup/rollup-linux-arm64-gnu --no-audit --no-fund

# Copy source code
COPY . .

# Set proper permissions
RUN chown -R node:node /app
USER node

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]