import express from "express";
import { logMiddleware } from "./utils/helper.js";

const app = express();

app.use(logMiddleware);

app.get("/", (_req, res) => {
  res.json({ status: "ok" });
});

export default app;
