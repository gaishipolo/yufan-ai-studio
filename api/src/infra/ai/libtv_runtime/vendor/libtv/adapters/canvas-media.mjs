// Experimental LibTV first-party uploads. Does not modify the pinned v1.0.0 adapter.
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { Cdp } from '../../tools/js-reverse-mcp/canvas-transport.mjs';

const BRIDGE='https://bridge.liblib.art/gateway/oss-server-api/oss-service/api/oss/pre-sign';
export const PART_SIZE=5*1024*1024;
export function objectPath(uuid,bytes,filename) {
  if (!/^[a-f0-9]{32}$/i.test(uuid)) throw new Error('Unexpected account upload UUID');
  const ext=path.extname(filename).toLowerCase();
  if (!['.png','.jpg','.jpeg','.webp','.mp4','.mp3','.wav'].includes(ext)) throw new Error('Unsupported upload extension');
  return `upload-images/${uuid}/${createHash('sha1').update(bytes).digest('hex')}${ext}`;
}
function httpsUrl(value) {
  const u=new URL(value);
  if(u.protocol!=='https:'||u.username||u.password||!['aliyuncs.com','liblib.art','liblib.cloud'].some(d=>u.hostname===d||u.hostname.endsWith('.'+d))) throw new Error('Unexpected upload/CDN destination');
  return u;
}
export async function accountUploadUuid(endpoint) {
  const cdp=await Cdp.connect(endpoint);
  try {
    const {cookies}=await cdp.send('Storage.getCookies');
    const value=cookies.find(c=>['www.liblib.tv','.liblib.tv','liblib.tv'].includes(c.domain)&&c.name==='useruuid')?.value;
    if(!value)throw new Error('Missing normal website upload UUID; sign in again');
    return value;
  } finally {cdp.close();}
}
export class MediaUploader {
  constructor(api,uuid,{fetcher=fetch,onEvent=()=>{}}={}) {this.api=api;this.uuid=uuid;this.fetcher=fetcher;this.onEvent=onEvent;}
  async sign(route,body) {
    const response=await this.fetcher(BRIDGE+route,{method:'POST',headers:{Token:this.api.headers.token,'Content-Type':'application/json',Origin:'https://www.liblib.tv',Referer:'https://www.liblib.tv/'},body:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(45000)});
    if(!response.ok)throw new Error(`Upload authorization HTTP ${response.status}`);
    const data=await response.json();
    if(data.code!==undefined&&data.code!==0)throw new Error(`Upload authorization business code ${data.code}`);
    return data.data;
  }
  async put(url,body,contentType) {
    httpsUrl(url);
    const response=await this.fetcher(url,{method:'PUT',body,headers:contentType?{'Content-Type':contentType}:{},redirect:'error',signal:AbortSignal.timeout(120000)});
    if(!response.ok)throw new Error(`Object upload HTTP ${response.status}`);
  }
  async upload(filename) {
    const bytes=await readFile(filename);
    const ext=path.extname(filename).toLowerCase();
    const contentType={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.mp4':'video/mp4','.mp3':'audio/mpeg','.wav':'audio/wav'}[ext];
    const key=objectPath(this.uuid,bytes,filename);
    let cdnUrl,partCount=1,mode='single';
    if(bytes.length<=PART_SIZE) {
      const data=await this.sign('/4',{path:key,contentType});
      const signedUrl=typeof data==='string'?data:data?.ossUrl;
      if(!signedUrl)throw new Error('Upload authorization omitted PUT URL');
      await this.put(signedUrl,bytes,contentType);
      cdnUrl=typeof data==='object'?data.cdnUrl:undefined;
    } else {
      mode='multipart';
      const init=await this.sign('/multipart/init/4',{path:key,fileSize:bytes.length,partSize:PART_SIZE,contentType});
      partCount=Math.ceil(bytes.length/PART_SIZE);
      if(!init?.uploadId||!Array.isArray(init.parts)||init.parts.length!==partCount)throw new Error('Unexpected multipart authorization response');
      const parts=[...init.parts].sort((a,b)=>a.partNumber-b.partNumber);
      for(let i=0;i<parts.length;i++) {
        if(parts[i].partNumber!==i+1 || (parts[i].method&&parts[i].method!=='PUT'))throw new Error('Unexpected multipart part ordering/method');
        await this.put(parts[i].url,bytes.subarray(i*PART_SIZE,Math.min((i+1)*PART_SIZE,bytes.length)));
        this.onEvent({stage:'uploaded_part',file:path.basename(filename),part:i+1,total:partCount});
      }
      const completed=await this.sign('/multipart/complete/4',{path:key,uploadId:init.uploadId});
      if(!completed?.path)throw new Error('Multipart completion omitted path');
      cdnUrl=completed.cdnUrl;
    }
    if(!cdnUrl)throw new Error('No auditable CDN URL returned; inspect normal upload behavior');
    httpsUrl(cdnUrl);
    // Exact uploaded bytes, independent of CDN MIME labels; never save PUT URLs.
    const fetched=await this.fetcher(cdnUrl,{headers:{Referer:'https://www.liblib.tv/'},signal:AbortSignal.timeout(120000)});
    if(!fetched.ok)throw new Error(`Uploaded-file verification HTTP ${fetched.status}`);
    const remote=Buffer.from(await fetched.arrayBuffer());
    const digest=createHash('sha256').update(bytes).digest('hex');
    if(createHash('sha256').update(remote).digest('hex')!==digest)throw new Error('CDN bytes differ from the uploaded local snapshot');
    let audit;
    if(contentType.startsWith('image/')) {
      const result=await this.api.request('/api/community/image/verify',{urlList:[cdnUrl]});
      const rows=result.data?.list;
      if(!Array.isArray(rows)||rows.length!==1||![0,false].includes(rows[0]?.result))throw new Error('Image audit did not explicitly pass');
      audit={type:'image',passed:true,response:result};
    } else {
      const type=contentType.startsWith('video/')?1:2;
      const started=await this.api.request('/api/community/video/audit',{videoUrl:cdnUrl,auditContentType:type});
      const processUuid=started.data?.processUuid;
      if(!processUuid)throw new Error('Media audit omitted process UUID');
      let result;
      for(let i=0;i<300;i++) {
        await delay(2000);
        result=await this.api.request('/api/community/video/auditResult',{processUuid});
        const status=Number(result.data?.auditStatus??result.data?.status??result.data?.auditProcessStatus);
        if(status===1)break;
        if(status===2||status===3)throw new Error(`Normal media audit failed (${status}); no generation submitted`);
        this.onEvent({stage:'auditing',file:path.basename(filename),attempt:i+1});
        if(i===299)throw new Error('Media audit timed out');
      }
      audit={type:type===1?'video':'audio',passed:true,process_uuid:processUuid,response:result};
    }
    return {file:path.basename(filename),bytes:bytes.length,sha256:digest,object_path:key,url:cdnUrl,mode,parts:partCount,cdn_bytes_verified:true,audit};
  }
}
