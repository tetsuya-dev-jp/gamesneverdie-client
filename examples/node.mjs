// Run after installing gnd on PATH. execFile does not invoke a shell.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const { stdout } = await promisify(execFile)('gnd', [
  'get', 'outer-wilds', '--fields', 'title,url,release_dates,platforms',
], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
console.log(JSON.parse(stdout));
