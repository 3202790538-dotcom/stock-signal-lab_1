import type {ProfileId,TemplateId} from './complete-strategy-types';

type StorageReader=Pick<Storage,'getItem'>;
export const COMPLETE_PREFERENCES_KEY='northstar-complete-strategy-v1';
export const TARGET_PREFERENCES_KEY='northstar-strategy-targets';

function record(key:string,storage?:StorageReader):Record<string,unknown>{
 try{
  const value:unknown=JSON.parse((storage??window.localStorage).getItem(key)||'null');
  return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
 }catch{return {}} // Private browsing and damaged preferences must not block research.
}
function within(value:unknown,min:number,max:number):value is number{
 return typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max;
}
export function readCompletePreferences(storage?:StorageReader):{templateId:TemplateId;riskProfile:ProfileId;target:string}{
 const saved=record(COMPLETE_PREFERENCES_KEY,storage);
 if(typeof saved.templateId==='string'&&['breakout','pullback','rotation'].includes(saved.templateId)&&
    typeof saved.riskProfile==='string'&&['conservative','balanced','aggressive'].includes(saved.riskProfile)&&within(saved.targetReturnPct,0,200)){
  return {templateId:saved.templateId as TemplateId,riskProfile:saved.riskProfile as ProfileId,target:String(saved.targetReturnPct)};
 }
 return {templateId:'breakout',riskProfile:'aggressive',target:'50'};
}
export function readTargetPreferences(storage?:StorageReader){
 const saved=record(TARGET_PREFERENCES_KEY,storage);
 return within(saved.targetReturnPct,0,200)&&within(saved.maxDrawdownPct,1,80)
  ?{returnTarget:String(saved.targetReturnPct),drawdownTarget:String(saved.maxDrawdownPct)}
  :{returnTarget:'50',drawdownTarget:'30'};
}
