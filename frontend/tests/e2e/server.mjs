// Starts the backend in simulation mode with a fresh data directory and the
// built UI for the end-to-end tests.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const port = process.argv[2] ?? '4180';
const root = resolve(import.meta.dirname, '../..');
const dataDir = mkdtempSync(join(tmpdir(), 'comcontrol-e2e-'));
const server = resolve(root, '../backend/.venv/bin/comcontrol-server');

const child = spawn(
	server,
	['--simulate', 'gaps', '--data-dir', dataDir, '--port', port, '--host', '127.0.0.1', '--log-level', 'warning'],
	{ stdio: 'inherit', env: { ...process.env, COMCONTROL_STATIC_DIR: resolve(root, 'build') } }
);

const cleanup = () => {
	child.kill('SIGTERM');
	rmSync(dataDir, { recursive: true, force: true });
};
process.on('SIGTERM', cleanup);
process.on('SIGINT', cleanup);
child.on('exit', (code) => {
	rmSync(dataDir, { recursive: true, force: true });
	process.exit(code ?? 0);
});
