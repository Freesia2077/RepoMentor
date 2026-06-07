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
  setTimeout(() => sendEvent('stage:progress', { stage: 'explorer', message: 'Scanning repository structure...' }), 1000);
  setTimeout(() => sendEvent('stage:progress', { stage: 'explorer', message: 'Identifying entry points...' }), 2000);
  setTimeout(() => sendEvent('stage:done', { 
    stage: 'explorer', 
    output: {
      projectType: { primary: "web-framework", secondary: [] },
      techStack: { language: "TypeScript", framework: "React", buildTool: "Vite" },
      fileCount: 42,
      entryPoints: [{ file: "src/main.tsx", role: "Entry" }],
      moduleMap: [{ path: "src/components", responsibility: "UI Elements", importance: "core", justification: "Renders UI" }],
      directorySummary: "Standard Vite React layout",
      projectSummary: "A frontend dashboard for RepoMentor."
    }
  }), 3500);

  setTimeout(() => sendEvent('stage:start', { stage: 'mentor' }), 4000);
  setTimeout(() => sendEvent('stage:progress', { stage: 'mentor', message: 'Analyzing architectural patterns...' }), 4500);
  setTimeout(() => sendEvent('interact:ask', { 
    questionId: 'q1', 
    stage: 'mentor', 
    question: 'Which architectural pattern would you like me to focus on in the report?', 
    options: ['State Management', 'Component Hierarchy', 'API Integration']
  }), 6000);

  // Pause here until answered
  if (!tasks[taskId]) tasks[taskId] = {};
  
  tasks[taskId].onAnswer = () => {
    // Resume sequence
    sendEvent('stage:progress', { stage: 'mentor', message: 'Received user preference. Continuing analysis...' });
    setTimeout(() => sendEvent('stage:done', { 
      stage: 'mentor', 
      output: {
        architectureOverview: "## Architecture\nThis is a standard React application using Vite.\n\n### Core Components\n- `App.tsx`: Main routing and state.\n- `ProgressUI.tsx`: Renders visual stages.",
        dependencyGraph: { "App.tsx": ["ProgressUI.tsx"] },
        readingPath: [{ step: 1, file: "src/App.tsx", why: "Main entry point." }],
        keyPatterns: [],
        codeConventions: []
      }
    }), 2000);

    setTimeout(() => sendEvent('stage:start', { stage: 'contributor' }), 2500);
    setTimeout(() => sendEvent('stage:progress', { stage: 'contributor', message: 'Generating setup guide and good first issues...' }), 3000);
    setTimeout(() => sendEvent('stage:done', { 
      stage: 'contributor', 
      output: {
        goodFirstIssues: [
          { area: "UI", difficulty: "easy", description: "Add a dark mode toggle button in the header." },
          { area: "Testing", difficulty: "medium", description: "Increase test coverage for App.tsx edge cases." }
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
