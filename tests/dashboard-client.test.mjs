import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readDashboard,refreshDashboard } from '../app/dashboard-client.ts';
import {dashboardFixture} from './fixtures/dashboard.mjs';

const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

test('refresh joins running job through lightweight status and fetches dashboard once', async () => {
  const paths = [];
  let polls = 0;
  const snapshot = dashboardFixture();
  const fetcher = async path => {
    paths.push(path);
    if (path === '/api/refresh') return json({}, 409);
    if (path === '/api/refresh-status') return json({ running: ++polls < 2, lastError: null });
    return json(snapshot);
  };
  const result = await refreshDashboard(new AbortController().signal, { fetcher, wait: async () => {} });
  assert.deepEqual(result, snapshot);
  assert.deepEqual(paths, ['/api/refresh', '/api/refresh-status', '/api/refresh-status', '/api/dashboard']);
});

test('cancelling refresh stops polling and prevents a dashboard download', async () => {
  const controller = new AbortController();
  const paths = [];
  const fetcher = async path => { paths.push(path); return json({}, 409); };
  await assert.rejects(refreshDashboard(controller.signal, { fetcher, wait: async () => controller.abort() }), { name: 'AbortError' });
  assert.deepEqual(paths, ['/api/refresh']);
});

test('reports a failed joined refresh without replacing available data', async () => {
  const paths = [];
  const fetcher = async path => { paths.push(path); return path === '/api/refresh' ? json({}, 409) : json({ running: false, lastError: 'Quote provider unavailable' }); };
  await assert.rejects(refreshDashboard(new AbortController().signal, { fetcher, wait: async () => {} }), /Quote provider unavailable/);
  assert.equal(paths.includes('/api/dashboard'), false);
});

test('successful refresh reads the dashboard once without polling', async () => {
  const paths = [];
  const fetcher = async path => { paths.push(path); return json(path === '/api/refresh' ? { ok: true } : dashboardFixture()); };
  await refreshDashboard(new AbortController().signal, { fetcher });
  assert.deepEqual(paths, ['/api/refresh', '/api/dashboard']);
});

test('incomplete or malformed dashboard is rejected before replacing working UI data',async()=>{
 const badBar=dashboardFixture();badBar.stocks[0].history[0].close=null;
 for(const value of [null,{stocks:[]},{...dashboardFixture(),stocks:[null]},{...dashboardFixture(),portfolio:null},badBar]){
  await assert.rejects(readDashboard(new AbortController().signal,async()=>json(value)),/行情数据格式/);
 }
});
