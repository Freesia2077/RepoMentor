# 🚀 RepoMentor

**RepoMentor** is an AI-powered open-source repository learning assistant. It integrates a powerful AI agent system with a modern web dashboard to help developers analyze, understand, and contribute to complex codebases seamlessly.

## ✨ Features

- **🤖 AI-Powered Analysis**: Deep-dive into repository architectures and module maps using advanced LLMs (Anthropic / DeepSeek).
- **📊 Real-time Dashboard**: A beautiful, interactive frontend to view analysis streams, module maps, and contribution guides.
- **⚡ Full-Stack Integration**: Built with Fastify (Node.js) on the backend and React + Vite on the frontend.
- **📂 Local SQLite Storage**: Fast, zero-config local storage to persist analysis history.
- **🛠 Git Native**: Leverages native Git operations for cloning and reading repositories.

## 💻 Tech Stack

- **Backend**: Node.js, TypeScript, Fastify, `better-sqlite3`, `@anthropic-ai/claude-agent-sdk`
- **Frontend**: React, TypeScript, Vite
- **Tooling**: `concurrently` for seamless monorepo-style dev server, Vitest for testing

## 🚀 Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) (v18+ recommended)
- [Git](https://git-scm.com/) installed on your machine

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Freesia2077/RepoMentor.git
   cd RepoMentor
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Setup environment variables:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in your AI provider's API key (e.g., DeepSeek / Anthropic).

### Running the App

Start both the backend and frontend dev servers concurrently:

```bash
npm run dev
```

The application will be available at `http://localhost:5173/` (Frontend) with the backend API running on `http://127.0.0.1:3000`.

### Building for Production

Compile both the frontend and backend:

```bash
npm run build
```

Start the production server:

```bash
npm start
```
The server will now host the frontend static files directly at `http://127.0.0.1:3000`.

## 🤝 Contributing

We welcome contributions! Please feel free to submit a Pull Request.

## 📝 License

MIT License
