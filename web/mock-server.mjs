import express from 'express';

const app = express();
app.use(express.json());

// In-memory state
const tasks = {};

// 1. Create task
app.post('/analysis', (req, res) => {
  const taskId = 'mock-task-' + Date.now();
  tasks[taskId] = { 
    status: 'cloning',
    answered: false,
    onAnswer: null
  };
  
  setTimeout(() => {
    if (tasks[taskId]) tasks[taskId].status = 'analyzing';
  }, 1000);

  res.json({
    taskId,
    status: 'cloning',
    createdAt: new Date().toISOString()
  });
});

// 2. Answer question
app.post('/analysis/:taskId/ask', (req, res) => {
  const { taskId } = req.params;
  
  if (!tasks[taskId]) return res.status(404).json({ error: 'Task not found' });
  
  tasks[taskId].answered = true;
  if (tasks[taskId].onAnswer) {
    tasks[taskId].onAnswer();
  }

  res.json({ accepted: true });
});

// 3. SSE Stream
app.get('/analysis/:taskId/stream', (req, res) => {
  const { taskId } = req.params;
  
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive'
  });

  const sendEvent = (type, data) => {
    res.write(`event: ${type}\n`);
    res.write(`data: ${JSON.stringify({ type, ...data })}\n\n`);
  };

  // Start sequence
  sendEvent('task:created', { taskId, status: 'analyzing' });

  setTimeout(() => sendEvent('stage:start', { stage: 'explorer' }), 500);
  setTimeout(() => sendEvent('stage:progress', { stage: 'explorer', message: '[Step 1/3] Cloning repository to secure sandbox...' }), 1000);
  setTimeout(() => sendEvent('stage:progress', { stage: 'explorer', message: '[Step 2/3] Scanning repository structure and AST...' }), 2000);
  setTimeout(() => sendEvent('stage:progress', { stage: 'explorer', message: '[Step 3/3] Identifying primary entry points and module boundaries...' }), 3000);
  setTimeout(() => sendEvent('stage:done', { 
    stage: 'explorer', 
    output: {
      projectType: { primary: "web-framework", secondary: [] },
      techStack: { language: "TypeScript", framework: "React", buildTool: "Vite" },
      fileCount: 42,
      entryPoints: [{ file: "src/main.tsx", role: "Entry" }],
      moduleMap: [
        { path: "src/components", responsibility: "Dumb UI components representing the atomic design layer.", importance: "core", justification: "Renders UI" },
        { path: "src/hooks", responsibility: "Custom hooks encapsulating state logic, SSE streaming, and side effects.", importance: "core", justification: "State management" },
        { path: "src/api", responsibility: "API client for backend communication and task orchestration.", importance: "secondary", justification: "Data fetching" },
        { path: "src/types", responsibility: "Shared TypeScript interfaces defining the Agent output shapes.", importance: "secondary", justification: "Type safety" },
        { path: "src/utils", responsibility: "Pure functions for data formatting and string manipulation.", importance: "minor", justification: "Utilities" },
        { path: "src/assets", responsibility: "Static assets, icons, and base stylesheets for the application.", importance: "minor", justification: "Assets" }
      ],
      directorySummary: "Standard Vite React layout with domain-driven boundaries.",
      projectSummary: "This repository implements a lightweight, reactive dashboard for visualizing the output of the RepoMentor Agent system. It handles Server-Sent Events (SSE) to render real-time progress and interact with users during architectural analysis."
    }
  }), 3500);

  setTimeout(() => sendEvent('stage:start', { stage: 'mentor' }), 4500);
  setTimeout(() => sendEvent('stage:progress', { stage: 'mentor', message: '[Step 1/2] Analyzing architectural patterns and dependencies...' }), 5000);
  setTimeout(() => sendEvent('interact:ask', { 
    questionId: 'q1', 
    stage: 'mentor', 
    question: 'Which architectural pattern would you like me to focus on in the report?', 
    options: ['State Management', 'Component Hierarchy', 'API Integration']
  }), 6500);

  // Pause here until answered
  if (!tasks[taskId]) tasks[taskId] = {};
  
  tasks[taskId].onAnswer = () => {
    // Resume sequence
    sendEvent('stage:progress', { stage: 'mentor', message: '[Step 2/2] Generating architecture documentation based on preference...' });
    setTimeout(() => sendEvent('stage:done', { 
      stage: 'mentor', 
      output: {
        architectureOverview: "## Architecture\nThis is a standard React application using Vite.\n\n### Core Components\n- `App.tsx`: Main routing and state management.\n- `ProgressUI.tsx`: Renders visual stages and interaction prompts.\n- `ReportSections.tsx`: Displays final analysis results.",
        dependencyGraph: { "App.tsx": ["ProgressUI.tsx", "ReportSections.tsx"] },
        readingPath: [
          { step: 1, file: "src/main.tsx", why: "Application entry point and root provider setup." },
          { step: 2, file: "src/App.tsx", why: "Core state machine orchestrating the analysis lifecycle." },
          { step: 3, file: "src/hooks/useAnalysisStream.ts", why: "Manages the SSE connection and parses Agent events." }
        ],
        keyPatterns: [],
        codeConventions: []
      }
    }), 2000);

    setTimeout(() => sendEvent('stage:start', { stage: 'contributor' }), 2500);
    setTimeout(() => sendEvent('stage:progress', { stage: 'contributor', message: '[Step 1/1] Generating setup guide and identifying good first issues...' }), 3000);
    setTimeout(() => sendEvent('stage:done', { 
      stage: 'contributor', 
      output: {
        goodFirstIssues: [
          { area: "UI", difficulty: "easy", description: "Add a dark mode toggle button in the header with local storage persistence." },
          { area: "Testing", difficulty: "medium", description: "Increase test coverage for App.tsx edge cases, particularly error boundaries." },
          { area: "Performance", difficulty: "medium", description: "Implement React.memo for Agent cards to prevent unnecessary re-renders." }
        ],
        contributionSetup: { devEnv: "npm install", build: "npm run build", test: "npm test", lint: "npm run lint" },
        entryFiles: [],
        notesForNewcomers: []
      }
    }), 5000);

    setTimeout(() => {
      sendEvent('task:completed', { taskId, summary: 'Analysis successfully completed!' });
      res.end();
    }, 6000);
  };

  req.on('close', () => {
    // cleanup
    res.end();
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Mock Server running on http://127.0.0.1:${PORT}`);
});
