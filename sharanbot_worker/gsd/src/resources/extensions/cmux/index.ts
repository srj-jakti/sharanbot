/**
 * Stub for cmux extension — removed for SharanBot Worker.
 * Provides no-op exports to satisfy TypeScript imports.
 */

export type CmuxLogLevel = "debug" | "info" | "warn" | "error" | "success" | "progress" | "warning";

export class CmuxClient {
  constructor(_config?: unknown) {}
  async connect(): Promise<void> {}
  async disconnect(): Promise<void> {}
  async send(_msg: string): Promise<void> {}
  async createSplit(_direction: string): Promise<string | null> { return null; }
  async sendSurface(_id: string, _cmd: string): Promise<boolean> { return false; }
  async createGridLayout(_count: number): Promise<string[]> { return []; }
  notify(_title: string, _message: string): boolean { return false; }
  getConfig(): { splits: boolean } { return { splits: false }; }
  getCapabilities(): Record<string, unknown> | null { return null; }

  static fromPreferences(_prefs?: unknown): CmuxClient {
    return new CmuxClient();
  }
}

export function shellEscape(s: string): string {
  return `'${s.replace(/'/g, "'\\''")}'`;
}

export function clearCmuxSidebar(_prefs?: unknown): void {}
export function logCmuxEvent(_prefs: unknown, _msg: string, _level?: CmuxLogLevel): void {}
export function syncCmuxSidebar(_prefs?: unknown, _state?: unknown): void {}
export function emitOsc777Notification(_title: string, _body: string): void {}
export function markCmuxPromptShown(): void {}
export function shouldPromptToEnableCmux(_prefs?: unknown): boolean { return false; }

export function detectCmuxEnvironment(): {
  detected: boolean;
  available: boolean;
  cliAvailable: boolean;
  socketPath: string | null;
  workspaceId: string | null;
  surfaceId: string | null;
} {
  return { detected: false, available: false, cliAvailable: false, socketPath: null, workspaceId: null, surfaceId: null };
}

export function resolveCmuxConfig(_prefs?: unknown): {
  enabled: boolean;
  notifications: boolean;
  sidebar: boolean;
  splits: boolean;
  browser: boolean;
  [key: string]: unknown;
} {
  return { enabled: false, notifications: false, sidebar: false, splits: false, browser: false };
}
