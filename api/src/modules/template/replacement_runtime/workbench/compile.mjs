import {compilePrompt} from '../prompt-compiler.mjs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
export async function compileWorkbench(spec){
  // 仅原片+文字场景描述：不伪造场景参考图，也不改变通用素材包编译规则。
  if(spec?.inputMode==='video_scene_text'){
    const refs=spec.references;
    const fixed=spec.fixed_prompt, description=spec.variables?.scene_prompt;
    if(spec.task!=='scene'||!Array.isArray(refs)||refs.length!==1||refs[0]?.role!=='source_video'||refs[0]?.type!=='video'
      ||typeof refs[0]?.id!=='string'||!refs[0].id||!refs[0].file)
      throw new Error('Text scene requires exactly one source video');
    if(typeof fixed!=='string'||fixed.split('{{ScenePrompt}}').length!==2
      ||typeof description!=='string'||!description.trim()||description.length>4000||description.includes('{{')||description.includes('}}'))
      throw new Error('Invalid scene description or fixed scene template');
    if(!Number.isInteger(spec.duration)||spec.duration<1||spec.duration>15
      ||!['9:16','16:9','1:1','3:4','4:3','adaptive'].includes(spec.aspect_ratio))
      throw new Error('Invalid scene video generation parameters');
    const prompt=fixed.replaceAll('{{ScenePrompt}}',()=>description);
    const tokens=[...prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(match=>match[1]);
    if(tokens.some(token=>token!=='Mixed 1'))throw new Error('Unknown scene prompt token');
    return {compiler_version:'scene-video-text-v1',task:'scene',model:'wanx3.0',modeType:'mixed2video',
      prompt,bindings:[{index:0,...refs[0],token:'{{Mixed 1}}'}],warnings:['场景描述来自本次输入，其余约束来自封装模板'],
      template_sha256:createHash('sha256').update(fixed).digest('hex'),
      spec_sha256:createHash('sha256').update(JSON.stringify(spec)).digest('hex'),generation_verified:false};
  }
  // The pinned generic compiler retains its historical 2-30s contract.
  // Adapt one-second workbench inputs without modifying the pinned release.
  const oneSecond=spec?.duration===1;
  const compiled=await compilePrompt(oneSecond?{...spec,duration:2}:spec);
  if(oneSecond){
    if(spec.task==='character') compiled.prompt=compiled.prompt.replace(/生成2秒、/, '生成1秒、');
    else compiled.prompt=compiled.prompt.replace(/输出约 2 秒、/, '输出约 1 秒、');
    compiled.spec_sha256=createHash('sha256').update(JSON.stringify(spec)).digest('hex');
    compiled.template_sha256=createHash('sha256').update(compiled.template_sha256+'workbench-one-second-v1').digest('hex');
    compiled.compiler_version+='+workbench-one-second-v1';
  }
  if(spec.task==='character'&&spec.wardrobe_mode==='reference'){
    const tokens=role=>compiled.bindings.filter(b=>b.role===role).map(b=>b.token).join('、');
    const video=tokens('source_video'),identity=tokens('identity'),audio=tokens('source_audio');
    const variables=spec.variables??{};
    const prompt=`任务：编辑完整原视频 ${video}，输出约${spec.duration}秒、${spec.aspect_ratio}。将${variables.source_subject}替换为${identity}中的同一目标人物。本任务明确允许采用人物参考的服装与整体造型，替换范围包括原主角身份和原主角穿着；不能只换衣服而保留原脸。\n\n人物与服装：${variables.identity_description??'以目标参考的五官、脸部轮廓、发型、肤色与身体比例为准。'} 多视图表示同一人，不增加人物，不复制角色卡的拼图排版或背景。服装按目标参考的款式、颜色、材质和覆盖范围自然适配，不增加裸露；目标身份在侧脸、低头与遮挡后连续一致。\n\n素材职责：${identity}提供目标人物身份及允许采用的服饰；${video}提供完整表演、时间与场景；${audio}提供同片原音轨。\n\n保留：沿用${video}的动作、转身、视线、表情变化、镜头运动、景别、剪辑时点、背景和光照；手持、盒内及手机屏幕中的非目标商品与道具继续保留原样，接触、抓握、遮挡和显露时机不变。人物身上服饰可按本任务更换，不将此授权扩展到手持商品或背景人物。\n${variables.state_constraints??''}\n\n声音：${audio}为完整原片音轨，沿用原台词、说话时段、节奏和相应口型，不新增台词、字幕、音乐或卖点。\n`;
    compiled.prompt=prompt;compiled.template_sha256=createHash('sha256').update('workbench-character-reference-v1').digest('hex');compiled.wardrobe_mode='reference';
  }
  /* 视频模板的固定提示词：spec.references 仍是唯一真相，bindings/mixedList 照常编译，
     只把最终文案换成模板原文。固定提示词允许「插入 3 张图、只点名三视图」，
     所以这里不做通用编译器那条「每个素材都要在提示词里被点名」的校验。 */
  const fixed=spec?.fixed_prompt;
  if(typeof fixed==='string'&&fixed.trim()){
    const scope=spec.variables?.replacement_scope??'';
    if(fixed.includes('{{ReplacementScope}}')&&(!scope.trim()||scope.includes('{{')||scope.includes('}}')))
      throw new Error('Fixed video template requires a replacement scope');
    const rendered=fixed.replaceAll('{{ReplacementScope}}',scope);
    const tokens=[...rendered.matchAll(/\{\{([^{}]+)\}\}/g)].map(m=>m[1]);
    if(tokens.some(t=>!/^Mixed \d+$/.test(t)||Number(t.slice(6))<1||Number(t.slice(6))>compiled.bindings.length))
      throw new Error('Fixed video template prompt has unresolved reference tokens');
    compiled.prompt=rendered;
    compiled.compiler_version+='+fixed-video-template-v1';
    compiled.template_sha256=createHash('sha256').update(fixed).digest('hex');
    compiled.warnings=[...(compiled.warnings??[]),'固定提示词来自视频模板，未套用通用模板文案'];
  }
  return compiled;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  let input='';for await(const chunk of process.stdin)input+=chunk;
  try{console.log(JSON.stringify(await compileWorkbench(JSON.parse(input))));}
  catch(e){console.error(e.message);process.exitCode=1;}
}
