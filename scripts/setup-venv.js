const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const venvPath = path.resolve(__dirname, '..', 'ai_engine', '.venv');
const pip = process.platform === 'win32'
  ? path.join(venvPath, 'Scripts', 'pip')
  : path.join(venvPath, 'bin', 'pip');
const reqPath = path.resolve(__dirname, '..', 'ai_engine', 'requirements.txt');

if (!fs.existsSync(venvPath)) {
  console.log('Creating venv...');
  execFileSync('python3', ['-m', 'venv', venvPath], { stdio: 'inherit' });
}

console.log('Installing requirements...');
execFileSync(pip, ['install', '-r', reqPath], { stdio: 'inherit' });
execFileSync(pip, ['install', 'pyinstaller'], { stdio: 'inherit' });
console.log('venv ready.');
