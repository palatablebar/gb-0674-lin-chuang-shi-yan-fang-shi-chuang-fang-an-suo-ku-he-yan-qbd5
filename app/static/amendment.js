(function(){
 const samples=JSON.parse(document.querySelector('#amendment-samples').textContent);
 const feedback=document.querySelector('#feedback');
 const runId=document.body.dataset.runId;
 const editor=document.querySelector('#amendment-json');
 const fill=key=>{editor.value=JSON.stringify(samples[key],null,2);};
 document.querySelector('#sample-shift').addEventListener('click',()=>fill('shift'));
 document.querySelector('#sample-deviation').addEventListener('click',()=>fill('deviation'));
 document.querySelector('#submit-amendment').addEventListener('click',async event=>{
  event.target.disabled=true;feedback.textContent='';
  try{
   let amendment;try{amendment=JSON.parse(editor.value);}catch(e){throw Error('更正内容不是有效JSON');}
   const r=await fetch('/api/amendments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:runId,amendment})});
   const data=await r.json();if(!r.ok)throw Error(data.detail);
   const url=new URL(location.href);url.searchParams.set('run_id',runId);url.searchParams.set('relock',data.id);location.href=url;
  }catch(e){feedback.textContent=e.message;event.target.disabled=false;}
 });
 const promote=document.querySelector('#promote-relock');
 if(promote){promote.addEventListener('click',async()=>{promote.disabled=true;feedback.textContent='';
  try{const r=await fetch('/api/amendments/'+promote.dataset.relock+'/promote',{method:'POST'});const data=await r.json();if(!r.ok)throw Error(data.detail);location.reload();}
  catch(e){feedback.textContent=e.message;promote.disabled=false;}});}
 const firstLock=document.querySelector('#first-lock');
 if(firstLock){firstLock.addEventListener('click',async()=>{firstLock.disabled=true;feedback.textContent='';
  try{const r=await fetch('/api/runs/'+runId+'/lock',{method:'POST'});const data=await r.json();if(!r.ok)throw Error(data.detail);location.reload();}
  catch(e){feedback.textContent=e.message;firstLock.disabled=false;}});}
})();
