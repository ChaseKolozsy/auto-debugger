import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
  type Tool,
  type ToolInputSchema,
} from '@modelcontextprotocol/sdk/types.js';
import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';

// Simple helpers
function openDb(dbPath?: string) {
  const p = dbPath && dbPath.trim() ? dbPath : path.join(process.cwd(), '.autodebug', 'line_reports.db');
  const dir = path.dirname(p);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const db = new Database(p, { fileMustExist: false, readonly: false });
  return { db, dbPath: p };
}

function rowToJson(row: any) {
  if (!row) return row;
  if (typeof row.variables === 'string') {
    try { row.variables = JSON.parse(row.variables); } catch {}
  }
  if (typeof row.variables_delta === 'string') {
    try { row.variables_delta = JSON.parse(row.variables_delta); } catch {}
  }
  return row;
}

const tools: Record<string, Tool> = {
  listSessions: {
    name: 'listSessions',
    description: 'List sessions with basic metadata',
    inputSchema: {
      type: 'object',
      properties: { db: { type: 'string', nullable: true } },
    } satisfies ToolInputSchema,
  },
  getSession: {
    name: 'getSession',
    description: 'Get one session summary by id',
    inputSchema: {
      type: 'object',
      required: ['sessionId'],
      properties: {
        db: { type: 'string', nullable: true },
        sessionId: { type: 'string' }
      },
    },
  },
  listLineReports: {
    name: 'listLineReports',
    description: 'List line reports for a session with paging and filters',
    inputSchema: {
      type: 'object',
      required: ['sessionId'],
      properties: {
        db: { type: 'string', nullable: true },
        sessionId: { type: 'string' },
        offset: { type: 'integer', nullable: true },
        limit: { type: 'integer', nullable: true },
        status: { type: 'string', enum: ['success','error','warning'], nullable: true },
        file: { type: 'string', nullable: true },
      },
    },
  },
  getLineReport: {
    name: 'getLineReport',
    description: 'Get a single line report by id',
    inputSchema: {
      type: 'object',
      required: ['id'],
      properties: { db: { type: 'string', nullable: true }, id: { type: 'integer' } },
    },
  },
  getCrashes: {
    name: 'getCrashes',
    description: 'List error line reports for a session',
    inputSchema: {
      type: 'object',
      required: ['sessionId'],
      properties: { db: { type: 'string', nullable: true }, sessionId: { type: 'string' } },
    },
  },
  addNote: {
    name: 'addNote',
    description: 'Add a note/observation to a specific line report (appends to existing observations)',
    inputSchema: {
      type: 'object',
      required: ['lineReportId', 'note'],
      properties: {
        db: { type: 'string', nullable: true },
        lineReportId: { type: 'integer', description: 'The line report ID to add a note to' },
        note: { type: 'string', description: 'The note/observation to add' },
        source: { type: 'string', default: 'llm', description: 'Source identifier (e.g., llm, agent, human)' }
      },
    },
  },
};

async function main() {
  const server = new Server({ name: 'autodebug-mcp', version: '0.1.0' }, { capabilities: { tools: {} } });

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: Object.values(tools) }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const { name, arguments: args } = req.params;
    try {
      switch (name) {
        case 'listSessions': {
          const { db: dbPath } = (args as any) || {};
          const { db } = openDb(dbPath);
          const rows = db.prepare(
            `SELECT session_id, file, language, start_time, end_time,
                    total_lines_executed, successful_lines, lines_with_errors, total_crashes, updated_at
             FROM session_summaries ORDER BY updated_at DESC`
          ).all();
          return { content: [{ type: 'json', json: rows }] };
        }
        case 'getSession': {
          const { db: dbPath, sessionId } = (args as any) || {};
          const { db } = openDb(dbPath);
          const row = db.prepare('SELECT * FROM session_summaries WHERE session_id = ?').get(sessionId);
          return { content: [{ type: 'json', json: row }] };
        }
        case 'listLineReports': {
          const { db: dbPath, sessionId, offset = 0, limit = 200, status, file } = (args as any) || {};
          const { db } = openDb(dbPath);
          const filters: string[] = ['session_id = ?'];
          const params: any[] = [sessionId];
          if (status) { filters.push('status = ?'); params.push(status); }
          if (file) { filters.push('file = ?'); params.push(file); }
          const where = filters.join(' AND ');
          const rows = db.prepare(
            `SELECT id, file, line_number, code, timestamp, variables, variables_delta, stack_depth, thread_id, status, error_type, error_message
             FROM line_reports WHERE ${where} ORDER BY id LIMIT ? OFFSET ?`
          ).all(...params, limit, offset).map(rowToJson);
          return { content: [{ type: 'json', json: rows }] };
        }
        case 'getLineReport': {
          const { db: dbPath, id } = (args as any) || {};
          const { db } = openDb(dbPath);
          const row = db.prepare(
            `SELECT id, session_id, file, line_number, code, timestamp, variables, variables_delta, stack_depth, thread_id, status, error_type, error_message
             FROM line_reports WHERE id = ?`
          ).get(id);
          return { content: [{ type: 'json', json: rowToJson(row) }] };
        }
        case 'getCrashes': {
          const { db: dbPath, sessionId } = (args as any) || {};
          const { db } = openDb(dbPath);
          const rows = db.prepare(
            `SELECT id, file, line_number, code, timestamp, error_type, error_message
             FROM line_reports WHERE session_id = ? AND status = 'error' ORDER BY id`
          ).all(sessionId);
          return { content: [{ type: 'json', json: rows }] };
        }
        case 'addNote': {
          const { db: dbPath, lineReportId, note, source = 'llm' } = (args as any) || {};
          const { db } = openDb(dbPath);
          
          // Get current observations
          const current = db.prepare('SELECT observations FROM line_reports WHERE id = ?').get(lineReportId) as any;
          if (!current) {
            return { isError: true, error: `Line report ${lineReportId} not found` } as any;
          }
          
          // Format the new note with timestamp and source
          const timestamp = new Date().toISOString();
          const formattedNote = `[${timestamp}] [${source}] ${note}\n`;
          
          // Append to existing observations
          const newObservations = (current.observations || '') + formattedNote;
          
          // Update the database
          const result = db.prepare('UPDATE line_reports SET observations = ? WHERE id = ?')
            .run(newObservations, lineReportId);
          
          return { 
            content: [{ 
              type: 'json', 
              json: { 
                success: true, 
                lineReportId, 
                changes: result.changes,
                note: formattedNote.trim()
              } 
            }] 
          };
        }
        default:
          return { isError: true, error: `Unknown tool ${name}` } as any;
      }
    } catch (e: any) {
      return { isError: true, error: e?.message || String(e) } as any;
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
