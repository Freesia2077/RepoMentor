import type { FastifyInstance } from "fastify";
import { z } from "zod";

import type { UpdateModelSettingsRequest } from "../types/index.js";
import {
  getPublicModelSettings,
  ModelSettingsError,
  saveModelSettings,
} from "../services/model-settings.js";

const updateSettingsSchema = z.object({
  provider: z.enum(["anthropic-compatible", "openai-compatible"]),
  baseUrl: z.string(),
  model: z.string(),
  apiKey: z.string().optional(),
});

export async function settingsRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/settings/model", async (_request, reply) => {
    reply.header("Cache-Control", "no-store");
    return reply.send(getPublicModelSettings());
  });

  app.put<{ Body: UpdateModelSettingsRequest }>(
    "/api/settings/model",
    async (request, reply) => {
      reply.header("Cache-Control", "no-store");
      if (!isLoopbackAddress(request.raw.socket.remoteAddress)) {
        return reply.status(403).send({
          error: "local_access_required",
          message: "模型设置只能从运行 RepoMentor 的本机修改",
        });
      }
      try {
        const input = updateSettingsSchema.parse(request.body);
        return reply.send(saveModelSettings(input));
      } catch (error) {
        if (error instanceof ModelSettingsError) {
          return reply.status(400).send({ error: error.code, message: error.message });
        }
        if (error instanceof z.ZodError) {
          return reply.status(400).send({
            error: "invalid_model_settings",
            message: error.issues.map((issue) => issue.message).join("; "),
          });
        }
        throw error;
      }
    },
  );
}

function isLoopbackAddress(address: string | undefined): boolean {
  if (!address) return false;
  const normalized = address.toLowerCase().split("%")[0]!;
  return normalized === "::1"
    || normalized === "0:0:0:0:0:0:0:1"
    || /^127(?:\.\d{1,3}){3}$/.test(normalized)
    || /^::ffff:127(?:\.\d{1,3}){3}$/.test(normalized);
}
