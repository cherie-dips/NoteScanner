FROM arm64v8/node:20-bookworm-slim

RUN npm install -g npm@latest

WORKDIR /app

COPY package*.json ./

RUN rm -rf node_modules package-lock.json && \
    npm cache clean --force && \
    npm install --legacy-peer-deps --platform=linux --arch=arm64 --no-audit --no-fund

RUN npm install --platform=linux --arch=arm64 @rollup/rollup-linux-arm64-gnu --no-audit --no-fund

COPY . .

RUN chown -R node:node /app
USER node

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
