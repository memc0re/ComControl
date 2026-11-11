// Starts the backend with a fresh data directory and the built UI for the
// end-to-end tests: simulation mode by default, the real controller only if
// COMCONTROL_HW_USB_PATH is set (hardware acceptance, playwright.hardware.config.ts).
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const port = process.argv[2] ?? '4180';
const root = resolve(import.meta.dirname, '../..');
const dataDir = mkdtempSync(join(tmpdir(), 'comcontrol-e2e-'));
const server = resolve(root, '../backend/.venv/bin/comcontrol-server');

// Real hardware only when explicitly selected (hardware acceptance config).
const hwPath = process.env.COMCONTROL_HW_USB_PATH;
const mode = hwPath ? [] : ['--simulate', 'gaps'];
const env = { ...process.env, COMCONTROL_STATIC_DIR: resolve(root, 'build') };
if (hwPath) {
	env.COMCONTROL_USB_PATH = hwPath;
	delete env.COMCONTROL_SIMULATE;
}
else delete env.COMCONTROL_USB_PATH;

const child = spawn(
	server,
	[...mode, '--data-dir', dataDir, '--port', port, '--host', '127.0.0.1', '--log-level', 'warning'],
	{ stdio: 'inherit', env }
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
