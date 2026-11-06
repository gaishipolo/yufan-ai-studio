import { readFile,mkdir,writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const ROOT=fileURLToPath(new URL('./',import.meta.url));
const CONFIG={
  character:{roles:['source_video','identity','source_frame','product_reference','source_audio'],required:'identity'},
  product:{roles:['source_video','target_product','source_frame','source_audio'],required:'target_product'},
  scene:{roles:['source_video','target_scene','source_frame','source_audio'],required:'target_scene'},
};
const TYPES={source_video:'video',identity:'image',source_frame:'image',product_reference:'image',target_product:'image',target_scene:'image',source_audio:'audio'};
const ROLES={source_video:'完整待编辑原片；提供动态表演、时间、镜头与非目标内容',identity:'目标人物身份；不采用参考图的穿搭、姿势与背景',source_frame:'原片真实关键帧；仅辅助构图和物品状态，不提供待替换人物身份',product_reference:'原片同款产品细节；用于保留外观，不控制物品出场时机',target_product:'目标替换产品；不采用图中人物、姿势、背景或文字',target_scene:'目标环境；不采用图中人物、商品、动作或文字',source_audio:'原片完整音轨；提供台词、节奏及说话时段，不添加新台词'};
const HASH=value=>createHash('sha256').update(value).digest('hex');
function text(value,name,fallback='') {
  const result=value===undefined?fallback:value;
  if(typeof result!=='string'||result.includes('{{')||result.includes('}}'))throw new Error(`${name} must be plain text without reference tokens`);
  return result.trim();
}
function requireText(value,name) {const result=text(value,name);if(!result)throw new Error(`${name} is required`);return result;}

export async function compilePrompt(spec) {
  const config=CONFIG[spec?.task];if(!config)throw new Error('task must be character, product or scene');
  if(!Number.isInteger(spec.duration)||spec.duration<2||spec.duration>30)throw new Error('duration must be 2-30 integer seconds');
  if(!['9:16','16:9','1:1','3:4','4:3','adaptive'].includes(spec.aspect_ratio))throw new Error('Invalid aspect_ratio');
  if(!Array.isArray(spec.references)||!spec.references.length||spec.references.length>15)throw new Error('Expected 1-15 references in final mixedList order');
  const ids=new Set();const bindings=spec.references.map((ref,index)=>{
    if(!ref||typeof ref.id!=='string'||!ref.id.trim()||ids.has(ref.id))throw new Error('Reference ids must be nonempty and unique');ids.add(ref.id);
    if(!config.roles.includes(ref.role)||ref.type!==TYPES[ref.role])throw new Error(`Invalid role/type: ${ref.role}`);
    if(!ref.file&&!ref.url)throw new Error('Each reference needs an actual local file path or uploaded URL');
    if(ref.url){const u=new URL(ref.url);if(u.protocol!=='https:'||u.username||u.password)throw new Error('Only HTTPS media URLs are supported');}
    // 封装时按用途写的说明优先于内置文案：同一用途下的多张图靠它区分职责。
    const usage=typeof ref.usage==='string'&&ref.usage.trim()?text(ref.usage,'usage').slice(0,300):'';
    return {index,id:ref.id,role:ref.role,type:ref.type,token:`{{Mixed ${index+1}}}`,...ref.file?{file:ref.file}:{},...ref.url?{url:ref.url}:{},...usage?{usage}:{}};
  });
  for(const [kind,max] of [['image',10],['video',5],['audio',5]])if(bindings.filter(b=>b.type===kind).length>max)throw new Error(`Too many ${kind} references`);
  const byRole=role=>bindings.filter(b=>b.role===role);
  if(byRole('source_video').length!==1)throw new Error('Exactly one complete source_video is required');
  if(byRole('source_audio').length>1||byRole('source_frame').length>1)throw new Error('At most one source_audio and source_frame is supported');
  if(!byRole(config.required).length)throw new Error(`Missing ${config.required} reference`);
  const tokens=role=>byRole(role).map(b=>b.token).join('、');
  const variables=spec.variables??{};
  const allowed=['source_subject','identity_description','source_product','target_product','replacement_scope','product_features','target_scene','protected_foreground','state_constraints'];
  if(!variables||typeof variables!=='object'||Array.isArray(variables)||Object.keys(variables).some(k=>!allowed.includes(k)))throw new Error('Unknown prompt variables');
  const audio=byRole('source_audio').length?`${tokens('source_audio')} 是 ${tokens('source_video')} 同片段原音轨，提供原口播、节奏及说话时间参考，口型跟随相应说话时刻。`:`声音以 ${tokens('source_video')} 的原音轨为准，沿用原说话者、台词、节奏和口型；原片无相应声音时不新增台词、音乐或音效。`;
  const frame=byRole('source_frame').length?`${tokens('source_frame')} 是原片真实关键帧，仅辅助构图与物品状态，不提供待替换人物身份。`:'';
  const values={duration:String(spec.duration),aspect_ratio:spec.aspect_ratio,source_video:tokens('source_video'),identity_refs:tokens('identity'),product_refs:tokens('target_product'),scene_refs:tokens('target_scene'),
    reference_bindings:bindings.map(b=>`${b.token}：${b.usage||ROLES[b.role]}。`).join('\n'),audio_instruction:audio,frame_instruction:frame,
    source_subject:spec.task==='character'?requireText(variables.source_subject,'source_subject'):text(variables.source_subject,'source_subject'),
    identity_description:text(variables.identity_description,'identity_description','以目标人物参考所示的稳定身份特征为准；面部近景用于身份，全身图只用于可见身体比例；多张人物参考须为同一人。'),
    source_product:spec.task==='product'?requireText(variables.source_product,'source_product'):text(variables.source_product,'source_product'),
    target_product:text(variables.target_product,'target_product','目标产品参考所示的同一款产品'),
    replacement_scope:spec.task==='product'?requireText(variables.replacement_scope,'replacement_scope'):text(variables.replacement_scope,'replacement_scope','原环境的背景空间、建筑、材质和陈设'),
    product_features:text(variables.product_features,'product_features','由目标产品参考的可见外观与结构确定，不臆造参考中未展示的设计。'),
    target_scene:text(variables.target_scene,'target_scene','目标场景参考所示的环境'),protected_foreground:text(variables.protected_foreground,'protected_foreground','原片人物、商品及与人物交互的全部前景对象。'),state_constraints:text(variables.state_constraints,'state_constraints'),
  };
  const templatePath=path.join(ROOT,'prompts',spec.task+'.template.zh.txt');
  const template=await readFile(templatePath,'utf8');
  const prompt=template.replace(/\{\{([a-z_]+)\}\}/g,(_,name)=>{if(!(name in values))throw new Error('Unresolved template variable '+name);return values[name];}).replace(/\n{3,}/g,'\n\n').trim()+'\n';
  const allTokens=[...prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(m=>m[1]);
  if(allTokens.some(t=>!/^Mixed \d+$/.test(t)||Number(t.slice(6))<1||Number(t.slice(6))>bindings.length))throw new Error('Unresolved or invalid reference token');
  if(bindings.some(b=>!prompt.includes(b.token)))throw new Error('An uploaded reference has no explicit prompt binding');
  const warnings=[];
  if(spec.task==='character'&&byRole('source_frame').length)warnings.push('source_frame contains the source subject and may compete with target identity; not enabled by default.');
  return {compiler_version:'1.0.0',task:spec.task,model:'wanx3.0',modeType:'mixed2video',prompt,bindings,warnings,template_sha256:HASH(template),spec_sha256:HASH(JSON.stringify(spec)),generation_verified:false};
}

export function verifyMixedList(compilation,mixedList) {
  if(!Array.isArray(mixedList)||mixedList.length!==compilation.bindings.length)throw new Error('mixedList count differs from compiled references');
  for(const [i,binding] of compilation.bindings.entries()) {
    if(mixedList[i].type!==binding.type||!mixedList[i].url)throw new Error(`mixedList type/URL mismatch at ${i}`);
    if(binding.url&&binding.url!==mixedList[i].url)throw new Error(`mixedList URL order mismatch at ${i}`);
  }
}

export async function writeCompilation(specFilename,outputDirectory) {
  const spec=JSON.parse(await readFile(specFilename,'utf8'));
  const compiled=await compilePrompt(spec);
  const out=path.resolve(outputDirectory);
  await mkdir(path.dirname(out),{recursive:true});await mkdir(out,{recursive:false});
  await writeFile(path.join(out,'prompt.txt'),compiled.prompt);
  await writeFile(path.join(out,'reference-bindings.json'),JSON.stringify(compiled.bindings,null,2)+'\n');
  await writeFile(path.join(out,'spec.json'),JSON.stringify(spec,null,2)+'\n');
  const {prompt,...metadata}=compiled;
  await writeFile(path.join(out,'compilation.json'),JSON.stringify({...metadata,status:'compiled_only_not_submitted'},null,2)+'\n');
  return {output:out,task:compiled.task,references:compiled.bindings.length,warnings:compiled.warnings,submitted:false};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [spec,out]=process.argv.slice(2);
  (async()=>{if(!spec||!out)throw new Error('Usage: node libtv/prompt-compiler.mjs SPEC.json NEW_OUTPUT_DIR');console.log(JSON.stringify(await writeCompilation(path.resolve(spec),path.resolve(out))));})().catch(e=>{console.error(e.message);process.exitCode=1;});
}
