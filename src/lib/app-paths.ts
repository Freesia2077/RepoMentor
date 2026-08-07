import path from "node:path";

const assetRoot = path.resolve(
  process.env.REPOMENTOR_ASSET_ROOT ?? process.cwd(),
);

export function resolveAppAsset(...segments: string[]): string {
  return path.join(assetRoot, ...segments);
}
