import type {PersonalPlanItem} from './personal-plan-types';

export type PlanGroup = 'all' | 'review' | 'conditional' | 'verify' | 'watch';
export const planGroupLabels:Record<Exclude<PlanGroup,'all'>,string>={review:'减持 / 退出复核',conditional:'条件买入',verify:'受限 / 待核验',watch:'持有 / 等待'};
export function planItemGroup(item:PersonalPlanItem,stale=false):Exclude<PlanGroup,'all'>{
  if(stale)return 'verify';
  if(item.status==='exit'||item.status==='reduce')return 'review';
  if(item.status==='conditional')return 'conditional';
  if(item.status==='verify'||item.status==='blocked')return 'verify';
  return 'watch';
}
export function planGroupCounts(items:PersonalPlanItem[],stale=false){
  const counts={review:0,conditional:0,verify:0,watch:0};
  for(const item of items)counts[planItemGroup(item,stale)]++;
  return counts;
}
/** Presentation sorting only: never recalculate, combine or reassign reserved quantities. */
export function visiblePlanItems(items:PersonalPlanItem[],group:PlanGroup,query:string,stale=false){
  const rank={review:0,conditional:1,verify:2,watch:3};
  const text=query.trim().toLowerCase();
  return items.filter(item=>(group==='all'||planItemGroup(item,stale)===group)&&`${item.symbol} ${item.name}`.toLowerCase().includes(text))
    .map((item,index)=>({item,index})).sort((a,b)=>rank[planItemGroup(a.item,stale)]-rank[planItemGroup(b.item,stale)]||a.index-b.index).map(({item})=>item);
}
