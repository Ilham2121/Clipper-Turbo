# Frontend dev image: Vite dev server with HMR.
#
# This is intentionally a *development* image (Vite dev server, not a built
# nginx bundle) so contributors get hot reload out of the box. The Vite proxy
# forwards /api to the backend service.
FROM node:22-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# --host exposes the dev server beyond the container loopback.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
