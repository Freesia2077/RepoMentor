import type Database from "better-sqlite3";
import type { ExperienceRecord } from "../../types/index.js";

const EXPERIENCE_COLUMNS = `
  id, owner, repo,
  primary_type AS primaryType,
  framework,
  secondary_type AS secondaryType,
  content,
  created_at
`;

export function findRelevant(
  db: Database.Database,
  primaryType: string,
  framework: string | null,
  secondaryTypes: string[],
  owner: string,
  limit = 5,
): ExperienceRecord[] {
  let rows: ExperienceRecord[];

  // 第一优先级：primary type + framework 完全匹配
  if (framework) {
    rows = db.prepare(`
      SELECT ${EXPERIENCE_COLUMNS} FROM experiences
      WHERE primary_type = ? AND framework = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(primaryType, framework, limit) as ExperienceRecord[];

    if (rows.length > 0) return rows;
  }

  // 第二优先级：primary type 匹配
  rows = db.prepare(`
    SELECT ${EXPERIENCE_COLUMNS} FROM experiences
    WHERE primary_type = ?
    ORDER BY created_at DESC
    LIMIT ?
  `).all(primaryType, limit) as ExperienceRecord[];

  if (rows.length > 0) return rows;

  // 第三优先级：framework 匹配 + secondary type 有交集
  if (framework && secondaryTypes.length > 0) {
    rows = db.prepare(`
      SELECT ${EXPERIENCE_COLUMNS} FROM experiences
      WHERE framework = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(framework, limit * 2) as ExperienceRecord[];

    const matching = rows.filter(r => {
      try {
        const secondary: string[] = JSON.parse(r.secondaryType);
        return secondary.some(s => secondaryTypes.includes(s));
      } catch { return false; }
    });

    if (matching.length > 0) return matching.slice(0, limit);
  }

  // 第四优先级：owner 匹配（弱参考）
  rows = db.prepare(`
    SELECT ${EXPERIENCE_COLUMNS} FROM experiences
    WHERE owner = ?
    ORDER BY created_at DESC
    LIMIT ?
  `).all(owner, limit) as ExperienceRecord[];

  return rows;
}

export function save(
  db: Database.Database,
  params: {
    owner: string;
    repo: string;
    primaryType: string;
    framework: string | null;
    secondaryType: string[];
    content: string;
  },
): void {
  db.prepare(`
    INSERT INTO experiences (owner, repo, primary_type, framework, secondary_type, content)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(
    params.owner,
    params.repo,
    params.primaryType,
    params.framework,
    JSON.stringify(params.secondaryType),
    params.content,
  );
}
