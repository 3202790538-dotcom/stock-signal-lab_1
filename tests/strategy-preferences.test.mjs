import assert from 'node:assert/strict';
import test from 'node:test';
import {readCompletePreferences,readTargetPreferences} from '../app/strategy-preferences.ts';

const storage=value=>({getItem:()=>value});
test('saved complete strategy choices survive remount',()=>{
 assert.deepEqual(readCompletePreferences(storage('{"templateId":"rotation","riskProfile":"balanced","targetReturnPct":25}')),
  {templateId:'rotation',riskProfile:'balanced',target:'25'});
});
test('malformed, disabled, or out-of-range storage cannot select invalid rules',()=>{
 for(const value of ['{','null','[]','{"templateId":"rotation","riskProfile":"balanced","targetReturnPct":201}',
  '{"templateId":"oops","riskProfile":"balanced","targetReturnPct":25}',
  '{"templateId":["rotation"],"riskProfile":"balanced","targetReturnPct":25}']){
  const result=readCompletePreferences(storage(value));
  assert.equal(result.templateId,'breakout');assert.equal(result.riskProfile,'aggressive');assert.equal(result.target,'50');
 }
 assert.equal(readCompletePreferences({getItem(){throw Error('blocked')}}).target,'50');
});
test('target preferences preserve valid boundary values and reject string numbers',()=>{
 assert.deepEqual(readTargetPreferences(storage('{"targetReturnPct":0,"maxDrawdownPct":80}')),{returnTarget:'0',drawdownTarget:'80'});
 assert.deepEqual(readTargetPreferences(storage('{"targetReturnPct":"0","maxDrawdownPct":80}')),{returnTarget:'50',drawdownTarget:'30'});
 assert.deepEqual(readTargetPreferences(storage('{"targetReturnPct":20,"maxDrawdownPct":0}')),{returnTarget:'50',drawdownTarget:'30'});
});
