import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chartGeometry, sparkPath } from '../app/market-chart-model.ts';

const bar = (date, close) => ({ date, open: close, high: close, low: close, close, volume: null, ma20: null, ma50: null, ma200: null });

test('one-day listings produce finite SVG coordinates and unique axis ticks', () => {
  const data = [bar('2026-11-09', 100)];
  const geometry = chartGeometry(data, true);
  assert.equal(geometry.tickIndices.length, 1);
  assert.equal(/NaN|Infinity/.test(geometry.paths.close), false);
  assert.equal(/NaN|Infinity/.test(sparkPath(data)), false);
  assert.ok(Number.isFinite(geometry.x(0)));
});

test('empty histories do not generate invalid geometry', () => {
  const geometry = chartGeometry([], true);
  assert.equal(geometry.paths.close, '');
  assert.deepEqual(geometry.tickIndices, []);
  assert.equal(sparkPath([]), '');
  assert.ok(Number.isFinite(geometry.y(0)));
});

test('missing moving averages break the path instead of joining missing observations', () => {
  const bars = [bar('2026-11-07', 90), bar('2026-11-08', 100), bar('2026-11-09', 110)];
  bars[0].ma20 = 95; bars[2].ma20 = 105;
  const path = chartGeometry(bars, true).paths.ma20;
  assert.equal((path.match(/M/g) || []).length, 2);
  assert.equal(path.includes('L'), false);
});
