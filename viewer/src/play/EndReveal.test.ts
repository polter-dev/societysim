// @vitest-environment jsdom
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {EndReveal,trustedWolves} from './EndReveal';
import type {Event,Label} from '../types/events';
const event=(seq:number,patch:Partial<Event>):Event=>({schema_version:3,game_id:'g',seq,t:seq,day:1,phase:'day',type:'statement',visibility:'public',actor:null,target:null,public_text:null,private_reasoning:null,suspicion:null,meta:{},...patch});
const events=[event(0,{type:'game_start',meta:{seats:{p0:{name:'You',model:'human',role:'villager',color:'#fff'},p1:{name:'Wolf',model:'model',role:'wolf',color:'#fff'}}}}),event(1,{actor:'p0',suspicion:[{player:'p1',p:.2}]}),event(2,{actor:'p1',private_reasoning:'SECRET REASONING',public_text:'Trust me.'}),event(3,{type:'game_end',meta:{winner:'wolves',roles:{p0:'villager',p1:'wolf'}}})];
const labels:Label[]=[{schema_version:3,game_id:'g',label:'false_role_claim',actor:'p1',source_seq:2,source:'engine',confidence:1,detail:'SECRET LABEL'}];
const html=(idx:number,mode:'villager'|'narrator')=>renderToStaticMarkup(createElement(EndReveal,{events,labels,idx,mode}));
it('renders no reveal or trusted wolf facts before game_end',()=>{expect(html(2,'narrator')).toBe('');expect(trustedWolves(events,2)).toEqual([]);});
it('keeps hidden reasoning and labels behind narrator view even after the public role reveal',()=>{const publicHtml=html(3,'villager');expect(publicHtml).toContain('The village revealed');expect(publicHtml).not.toContain('SECRET REASONING');expect(publicHtml).not.toContain('SECRET LABEL');expect(publicHtml).toContain('mode=narrator');const full=html(3,'narrator');expect(full).toContain('SECRET REASONING');expect(full).toContain('SECRET LABEL');expect(full).toContain('Play again');expect(full).toContain('Copy share link');});
it('compares only the human’s latest recorded suspicions with the actual revealed wolves',()=>{expect(trustedWolves(events,3)).toEqual([{id:'p1',name:'Wolf',p:.2}]);const later=[...events.slice(0,3),event(3,{actor:'p0',suspicion:[{player:'p1',p:.9}]}),{...events[3],seq:4}];expect(trustedWolves(later,4)).toEqual([]);expect(html(3,'narrator')).toContain('You rated them 20%');});
