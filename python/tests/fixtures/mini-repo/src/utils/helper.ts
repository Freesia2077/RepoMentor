import type { Request, Response, NextFunction } from "express";

// TODO: add request timing
export function logMiddleware(req: Request, _res: Response, next: NextFunction): void {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.url}`);
  next();
}

// FIXME: error handling is incomplete
export function sendError(res: Response, message: string, statusCode = 500): void {
  res.status(statusCode).json({ error: message });
}
