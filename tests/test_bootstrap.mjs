import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const installer = await readFile(new URL('../bootstrap/install.sh', import.meta.url), 'utf8');
const source = (await readFile(new URL('../bootstrap/worker.js', import.meta.url), 'utf8'))
    .replace("import INSTALLER from './install.sh';", 'const INSTALLER=' + JSON.stringify(installer) + ';');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const get = await worker.fetch(new Request('https://install.example.com/'));
assert.equal(get.status, 200);
assert.equal(await get.text(), installer);
assert.equal(get.headers.get('cache-control'), 'no-store');
const head = await worker.fetch(new Request('https://install.example.com/', {method: 'HEAD'}));
assert.equal(head.status, 200);assert.equal(await head.text(), '');
assert.equal((await worker.fetch(new Request('https://install.example.com/api/machines'))).status, 404);
assert.equal((await worker.fetch(new Request('https://install.example.com/', {method: 'POST'}))).status, 405);
console.log('Public bootstrap: exact script response, HEAD, isolated paths and methods passed.');
