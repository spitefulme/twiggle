// Unicode17 NFC and extended graphemes. No runtime-version native fallback.
import data from './unicode17/data.mjs';
export const unicodeVersion=data.version;

function property(code,ranges,fallback='Other'){
 let lo=0,hi=ranges.length-1;
 while(lo<=hi){const mid=(lo+hi)>>1,row=ranges[mid];
  if(code<row[0])hi=mid-1;else if(code>row[1])lo=mid+1;else return row[2];
 }
 return fallback;
}
const gcb=code=>property(code,data.gcb);
const incb=code=>property(code,data.incb);
const pict=code=>property(code,data.pictographic)==='Extended_Pictographic';
const ccc=code=>data.combining[code]??0;

function decompose(code,out){
 if(code>=0xAC00&&code<0xD7A4){
  const offset=code-0xAC00;
  out.push(0x1100+Math.floor(offset/588),0x1161+Math.floor(offset%588/28));
  if(offset%28)out.push(0x11A7+offset%28);
 }else if(data.decomposition[code]){
  for(const child of data.decomposition[code])decompose(child,out);
 }else out.push(code);
}
function composite(left,right){
 if(left>=0x1100&&left<=0x1112&&right>=0x1161&&right<=0x1175)
  return 0xAC00+(left-0x1100)*588+(right-0x1161)*28;
 if(left>=0xAC00&&left<0xD7A4&&(left-0xAC00)%28===0&&right>=0x11A8&&right<=0x11C2)
  return left+right-0x11A7;
 return data.composition[`${left},${right}`];
}
export function normalizeNFC(value){
 const codes=[];
 for(const ch of value)decompose(ch.codePointAt(0),codes);
 for(let i=1;i<codes.length;i++){
  const rank=ccc(codes[i]);let j=i;
  while(rank&&j>0&&ccc(codes[j-1])>rank){[codes[j],codes[j-1]]=[codes[j-1],codes[j]];j--;}
 }
 const out=[];let starter=-1,last=0;
 for(const code of codes){
  const rank=ccc(code),merged=starter<0?undefined:composite(out[starter],code);
  if(merged!==undefined&&(last<rank||last===0))out[starter]=merged;
  else{if(rank===0)starter=out.length;out.push(code);last=rank;}
 }
 return out.map(code=>String.fromCodePoint(code)).join('');
}

function indicJoin(codes,index){
 if(incb(codes[index])!=='InCB:Consonant')return false;
 let i=index-1,linker=false;
 while(i>=0&&['InCB:Extend','InCB:Linker'].includes(incb(codes[i]))){
  linker ||= incb(codes[i])==='InCB:Linker';i--;
 }
 return linker&&i>=0&&incb(codes[i])==='InCB:Consonant';
}
function emojiJoin(codes,index){
 if(!pict(codes[index])||gcb(codes[index-1])!=='ZWJ')return false;
 let i=index-2;
 while(i>=0&&gcb(codes[i])==='Extend')i--;
 return i>=0&&pict(codes[i]);
}
function joins(codes,index){
 const left=gcb(codes[index-1]),right=gcb(codes[index]);
 if(left==='CR'&&right==='LF')return true;
 if(['Control','CR','LF'].includes(left)||['Control','CR','LF'].includes(right))return false;
 if(left==='L'&&['L','V','LV','LVT'].includes(right))return true;
 if(['LV','V'].includes(left)&&['V','T'].includes(right))return true;
 if(['LVT','T'].includes(left)&&right==='T')return true;
 if(['Extend','ZWJ','SpacingMark'].includes(right)||left==='Prepend')return true;
 if(indicJoin(codes,index)||emojiJoin(codes,index))return true;
 if(left==='Regional_Indicator'&&right==='Regional_Indicator'){
  let count=0;
  for(let i=index-1;i>=0&&gcb(codes[i])==='Regional_Indicator';i--)count++;
  return count%2===1;
 }
 return false;
}
export function graphemes(value){
 const codes=Array.from(value,ch=>ch.codePointAt(0));
 if(!codes.length)return [];
 const groups=[[codes[0]]];
 for(let i=1;i<codes.length;i++){
  if(joins(codes,i))groups.at(-1).push(codes[i]);else groups.push([codes[i]]);
 }
 return groups.map(group=>group.map(code=>String.fromCodePoint(code)).join(''));
}
export function tokens(value){
 const result=[];let token='';
 for(const ch of normalizeNFC(value)){
  if(property(ch.codePointAt(0),data.whitespace)==='White_Space'){
   if(token)result.push(token);token='';
  }else token+=ch;
 }
 if(token)result.push(token);
 return result;
}
export function validateLabel(value,{participant=false,limit=120,empty=false}={}){
 if(typeof value!=='string')return {valid:false,code:'INVALID_LABEL'};
 for(const ch of value)if(property(ch.codePointAt(0),data.unsafe)==='unsafe'&&!['\u200c','\u200d'].includes(ch))
  return {valid:false,code:'UNSAFE_TEXT'};
 const words=tokens(value),count=graphemes(value).length;
 if(!words.length&&!empty)return {valid:false,code:'LABEL_REQUIRED'};
 if(count>limit)return {valid:false,code:'LABEL_LENGTH_LIMIT'};
 if(participant&&(words.length<1||words.length>7))return {valid:false,code:'LABEL_TOKEN_LIMIT'};
 return {valid:true,raw:value,tokens:words.length,graphemes:count};
}
