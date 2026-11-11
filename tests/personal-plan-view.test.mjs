import assert from 'node:assert/strict';
import {test} from 'node:test';
import {planGroupCounts,visiblePlanItems} from '../app/personal-plan-view.ts';
const row=(id,status,quantity)=>({id,symbol:id,name:`Company ${id}`,status,quantity});
test('presentation prioritizes review while preserving backend reservations and order within a group',()=>{
 const items=[row('WAIT','wait',0),row('BUYB','conditional',3),row('EXIT','exit',0),row('BUYA','conditional',7),row('DATA','verify',null)];
 const before=JSON.stringify(items);
 const shown=visiblePlanItems(items,'all','');
 assert.deepEqual(shown.map(x=>x.id),['EXIT','BUYB','BUYA','DATA','WAIT']);
 assert.equal(shown[1],items[1]);
 assert.equal(shown[1].quantity,3);
 assert.equal(JSON.stringify(items),before);
});
test('stale plans lose actionable categories without altering underlying decisions',()=>{
 const items=[row('EXIT','exit',0),row('BUY','conditional',3),row('HOLD','hold',0)];
 assert.deepEqual(planGroupCounts(items,true),{review:0,conditional:0,verify:3,watch:0});
 assert.equal(visiblePlanItems(items,'conditional','',true).length,0);
 assert.equal(items[1].quantity,3);
});
test('category and company search combine without treating null quantity as a buy',()=>{
 const items=[row('ABC','blocked',0),row('DEF','verify',null),row('GHI','conditional',4)];
 assert.deepEqual(visiblePlanItems(items,'verify','company d').map(x=>x.id),['DEF']);
 assert.deepEqual(planGroupCounts(items),{review:0,conditional:1,verify:2,watch:0});
});
