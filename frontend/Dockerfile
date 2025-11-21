FROM node:18-alpine

WORKDIR /app

COPY package*.json ./

RUN npm install

COPY . .

EXPOSE 5173

# --host é necessário para expor fora do container
CMD ["npm", "run", "dev", "--", "--host"]