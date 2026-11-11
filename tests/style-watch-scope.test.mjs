import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import postcss from 'postcss';
import tailwind from '@tailwindcss/postcss';

test('style compilation watches UI sources without watching quote caches or docs',async()=>{
  const root=fileURLToPath(new URL('../',import.meta.url));
  const from=path.join(root,'app/globals.css');
  const result=await postcss([tailwind()]).process(await readFile(from,'utf8'),{from});
  const watched=result.messages.filter(message=>message.type==='dir-dependency').map(message=>message.dir);
  assert.ok(watched.includes(path.join(root,'app')));
  assert.ok(watched.includes(path.join(root,'components')));
  const allowed=['app','components','hooks','lib'].map(folder=>path.join(root,folder));
  assert.deepEqual(watched.filter(folder=>!allowed.some(ui=>folder===ui||folder.startsWith(ui+path.sep))),[]);
  assert.ok(result.css.includes('.sr-only'));
});
