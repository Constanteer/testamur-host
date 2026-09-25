/* Product-facing MathHub home + source ingest.
   Lean details remain inspectable, but they are not the first interaction. */
state.sourceIngest = state.sourceIngest || {loading:false,error:"",result:null,selected:-1};
state.sourceDraft = state.sourceDraft || {title:"",statement:"",imports:"Mathlib",declaration:"",proof:""};
state.sourceRegister = state.sourceRegister || {loading:false,error:"",success:null};
state.sourceCheck = state.sourceCheck || {running:false,result:null,error:""};

function mhHumanize(value){
  return String(value || "").split(".").pop().replace(/[_-]+/g," ").replace(/([a-z0-9])([A-Z])/g,"$1 $2").replace(/\b\w/g,function(c){return c.toUpperCase();});
}
function mhRuntimeTone(){
  if(!state.runtime.checked)return "working";
  if(state.runtime.ready)return "ready";
  if(state.runtime.setupRunning)return "working";
  return "bad";
}
function mhRuntimeCopy(){
  if(state.runtime.ready)return "Lean + Mathlib ready";
  if(state.runtime.setupRunning)return state.runtime.label || "Preparing Lean + Mathlib automatically";
  if(state.runtime.setupError)return "Math library setup needs attention";
  return state.runtime.label || "Checking math library";
}

topbar=function(){
  var hosted=location.pathname.startsWith("/sources/mathhub");
  var parent=hosted?'<a class="parent-link" href="/">Testamur</a><span class="product-separator">/</span>':"";
  return '<header class="topbar mathhub-product-bar"><div class="topbar-inner">'+
    '<div class="product-lockup">'+parent+'<button class="wordmark" data-view="registry">MathHub</button></div>'+
    '<nav class="topnav" aria-label="MathHub navigation">'+
      '<button class="'+(state.view==="registry"?"active":"")+'" data-view="registry">Your MathHub</button>'+
      '<button data-library-browser>Math library</button>'+
      '<button class="'+(state.view==="graph"?"active":"")+'" data-view="graph">Connections</button>'+
    '</nav><div class="top-spacer"></div>'+
    '<div class="runtime-pill" title="Lean and Mathlib status"><span class="runtime-dot '+mhRuntimeTone()+'"></span><span>'+esc(mhRuntimeCopy())+'</span></div>'+
    '<button class="btn btn-primary btn-small" data-view="submit">Add Lean</button>'+
  '</div></header>';
};

function mhOwnedClaims(){
  if(typeof registryState==="object"){
    return claims.filter(function(c){return c.registry===true;});
  }
  return [];
}

renderRegistry=function(){
  var q=state.search.trim().toLowerCase();
  var owned=mhOwnedClaims();
  var filtered=owned.filter(function(c){
    return (state.filter==="all"||c.status===state.filter)&&(!q||[c.id,c.title,c.statement,c.area,c.author,c.formalizer].concat(c.tags||[]).join(" ").toLowerCase().includes(q));
  });
  var verified=owned.filter(function(c){return c.status==="verified";}).length;
  var rows=filtered.length?filtered.map(function(c){
    return '<button class="mathhub-owned-row" data-claim="'+esc(c.id)+'"><div>'+
      '<span class="mathhub-owned-area">'+esc(c.area||"Mathematics")+'</span>'+
      '<strong>'+esc(c.title)+'</strong><p>'+esc(c.statement||"")+'</p></div>'+
      '<div class="mathhub-owned-meta">'+badge(c.status)+'<span>→</span></div></button>';
  }).join(""):'<div class="mathhub-owned-empty"><strong>Nothing here yet.</strong><span>Browse Mathlib, or add your own Lean source to create a workspace record.</span></div>';
  var runtimeText=state.runtime.ready?
    "The pinned environment is ready. Searches, imports and proof builds use this exact Lean/Mathlib environment.":
    (state.runtime.setupError?
      "Automatic setup stopped. Open the Math library and retry setup; no terminal commands are required.":
      "First launch can take a while. Lean, Mathlib and the compiled cache are downloaded in the background and kept on persistent storage.");
  var setupProgress=(state.runtime.setupRunning&&state.runtime.setupSteps)?
    '<div class="mathhub-home-progress"><div><span>'+esc(state.runtime.label||"Preparing math library")+'</span><strong>'+Number(state.runtime.setupStep||0)+' / '+Number(state.runtime.setupSteps||0)+'</strong></div><progress max="'+Number(state.runtime.setupSteps||1)+'" value="'+Number(state.runtime.setupStep||0)+'"></progress></div>':"";
  var registryLoading=typeof registryState==="object"&&!registryState.ready;
  return '<main class="shell page mathhub-home">'+
    '<section class="mathhub-home-hero"><div class="mathhub-home-copy">'+
      '<div class="eyebrow">Formal mathematics, made navigable</div>'+
      '<h1>A working map of mathematics you can inspect.</h1>'+
      '<p>Start from Mathlib, bring a Lean file, or paste a public source link. MathHub keeps the exact declarations, proofs and dependencies underneath without making you begin with them.</p>'+
      '<div class="mathhub-home-actions"><button class="btn btn-primary mathhub-hero-button" data-library-browser>Browse the math library</button><button class="btn mathhub-hero-button" data-view="submit">Add Lean</button></div>'+
    '</div><aside class="mathhub-home-runtime">'+
      '<div><div class="mathhub-runtime-line"><span class="runtime-dot '+mhRuntimeTone()+'"></span><strong>'+esc(mhRuntimeCopy())+'</strong></div><p>'+esc(runtimeText)+'</p>'+setupProgress+'</div>'+
      '<div class="mathhub-runtime-facts"><span><strong>'+owned.length+'</strong> statements</span><span><strong>'+verified+'</strong> verified builds</span></div>'+
    '</aside></section>'+
    '<section class="mathhub-start-grid">'+
      '<button class="mathhub-start-card primary" data-library-browser><span class="mathhub-start-index">01</span><strong>Find mathematics</strong><p>Browse ordinary subjects and search Mathlib without knowing a namespace first.</p><span class="mathhub-start-link">Open library →</span></button>'+
      '<button class="mathhub-start-card" data-view="submit"><span class="mathhub-start-index">02</span><strong>Add Lean</strong><p>Drop a <code>.lean</code> file, paste a GitHub/raw URL, or write a declaration. This is the path for new user-provided formal source.</p><span class="mathhub-start-link">Add source →</span></button>'+
      '<button class="mathhub-start-card" data-view="graph"><span class="mathhub-start-index">03</span><strong>Follow connections</strong><p>See which statements a proof actually depends on and move through the graph.</p><span class="mathhub-start-link">Open connections →</span></button>'+
    '</section>'+
    '<section class="mathhub-owned"><div class="mathhub-owned-head"><div><div class="eyebrow">Your MathHub</div><h2>Collected statements</h2></div>'+
      '<label class="mathhub-inline-search"><span>⌕</span><input id="search" value="'+esc(state.search)+'" placeholder="Search what you have added…"/></label></div>'+
      '<div class="mathhub-owned-list">'+(registryLoading?'<div class="mathhub-owned-loading"><span class="runtime-dot working"></span><div><strong>Loading your MathHub…</strong><span>Reading the persistent registry.</span></div></div>':rows)+'</div>'+
    '</section></main>';
};

function mhIngestResult(){
  var s=state.sourceIngest;
  if(s.loading)return '<div class="mathhub-ingest-wait"><span class="runtime-dot working"></span><div><strong>Reading source…</strong><span>Extracting imports and top-level declarations.</span></div></div>';
  if(s.error)return '<div class="callout bad"><strong>Could not read source.</strong><br>'+esc(s.error)+'</div>';
  if(!s.result)return '<div class="mathhub-ingest-empty"><strong>No source loaded yet.</strong><span>Upload a Lean file or fetch one from a public URL.</span></div>';
  var r=s.result,candidates=r.candidates||[];
  var list=candidates.length?'<div class="mathhub-candidate-list">'+candidates.map(function(item,index){
    var chosen=s.selected===index;
    return '<button type="button" class="mathhub-candidate '+(chosen?"selected":"")+'" data-ingest-candidate="'+index+'"><div>'+
      '<span>'+esc(item.kind)+'</span><strong>'+esc(mhHumanize(item.qualified_name||item.name))+'</strong><code>'+esc(item.qualified_name||item.name)+'</code></div>'+
      '<span>'+(chosen?"Loaded":"Use")+' →</span></button>';
  }).join("")+'</div>':'<div class="callout warn"><strong>No theorem, lemma or definition was detected.</strong><br>The file is saved, but you can still use the manual editor below.</div>';
  return '<div class="mathhub-ingest-result"><div class="mathhub-source-head"><div><span>'+esc(r.filename)+'</span><strong>'+candidates.length+' declaration'+(candidates.length===1?"":"s")+' found</strong></div><code>'+esc(r.source_id)+'</code></div>'+
    list+
    '<details class="mathhub-source-details"><summary>Source details</summary><div><span>Imports</span><code>'+esc((r.imports||[]).join("\n"))+'</code></div><div><span>SHA-256</span><code>'+esc(r.sha256||"")+'</code></div>'+
    (r.source_url?'<div><span>Downloaded from</span><code>'+esc(r.source_url)+'</code></div>':"")+'</details></div>';
}

renderSubmit=function(){
  var d=state.sourceDraft,reg=state.sourceRegister;
  return '<main class="shell page mathhub-import-page">'+
    '<section class="mathhub-import-head"><div><div class="eyebrow">Add Lean</div><h1>Add new Lean source to your MathHub.</h1><p>Upload a <code>.lean</code> file, fetch a public source URL, or write one manually. Existing Mathlib theorems are already in the library and do not need to be added.</p></div><button class="btn" data-library-browser>Browse existing Mathlib</button></section>'+
    '<div class="mathhub-ingest-layout"><section class="mathhub-ingest-panel"><div class="mathhub-ingest-choice">'+
      '<label class="mathhub-dropzone" for="mathhub-source-file"><input id="mathhub-source-file" type="file" accept=".lean,text/plain"/><span class="mathhub-drop-icon">↑</span><strong>Drop or choose a .lean file</strong><span>UTF-8 · up to about 450 KB</span></label>'+
      '<div class="mathhub-ingest-or"><span>or</span></div>'+
      '<form id="mathhub-source-url-form" class="mathhub-url-form"><label><span>Public source URL</span><div><input id="mathhub-source-url" type="url" placeholder="https://github.com/…/blob/…/File.lean" required/><button class="btn btn-primary" type="submit">Fetch</button></div></label><small>GitHub blob links are converted automatically. Private/local network URLs are rejected.</small></form>'+
    '</div></section><section class="mathhub-ingest-preview">'+mhIngestResult()+'</section></div>'+
    '<section class="mathhub-register-review"><div class="mathhub-review-head"><div><div class="eyebrow">Review new source</div><h2>'+(state.sourceIngest.result?"Review the selected declaration":"Write Lean manually")+'</h2><p>This draft is editable before it is added. Exact Lean remains the object MathHub verifies and records.</p></div></div>'+
      '<form id="source-claim-form" class="mathhub-review-form"><div class="mathhub-human-fields">'+
        '<label><span>Title</span><input id="draft-title" required value="'+esc(d.title)+'" placeholder="A readable theorem title"/></label>'+
        '<label><span>What does it say?</span><textarea id="draft-human" required placeholder="Describe the mathematical statement in ordinary language.">'+esc(d.statement)+'</textarea></label>'+
      '</div><details class="mathhub-formal-details" '+(d.declaration?"open":"")+'><summary>Formal Lean details</summary><div class="mathhub-formal-fields">'+
        '<label><span>Imports</span><textarea id="draft-imports">'+esc(d.imports||"Mathlib")+'</textarea></label>'+
        '<label><span>Declaration</span><textarea id="draft-decl" required>'+esc(d.declaration)+'</textarea></label>'+
        '<label><span>Proof</span><textarea id="draft-proof" class="proof-draft" placeholder="by&#10;  ...">'+esc(d.proof)+'</textarea></label>'+
      '</div></details><div class="mathhub-review-actions"><button class="btn" type="button" id="source-draft-test" '+(state.runtime.ready&&!state.sourceCheck.running?"":"disabled")+'>'+(state.sourceCheck.running?"Checking…":"Check with Lean")+'</button><button class="btn btn-primary" type="submit" '+(reg.loading?"disabled":"")+'>'+(reg.loading?"Adding…":"Add to MathHub")+'</button></div>'+
      '<div id="draft-result" class="draft-result">'+
        (state.sourceCheck.error?'<div class="callout bad"><strong>Lean check failed.</strong><br>'+esc(state.sourceCheck.error)+'</div>':"")+
        (state.sourceCheck.result?'<div class="callout '+(state.sourceCheck.result.ok&&!state.sourceCheck.result.contains_sorry?"":"warn")+'"><strong>'+(state.sourceCheck.result.ok&&!state.sourceCheck.result.contains_sorry?"Lean check passed":"Not verified yet")+'</strong><br>'+(state.sourceCheck.result.contains_sorry?"The proof still contains sorry.":"The current formalization was checked in the pinned environment.")+'</div>':"")+
        (reg.error?'<div class="callout bad">'+esc(reg.error)+'</div>':"")+
        (reg.success?'<div class="callout"><strong>Added.</strong><br>'+esc(reg.success)+'</div>':"")+
      '</div></form>'+
    '</section></main>';
};

async function mhPost(path,payload){
  var response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  var data={};try{data=await response.json();}catch(_){}
  if(!response.ok)throw new Error(data.error||data.message||("HTTP "+response.status));
  return data;
}
function mhApplyCandidate(index,rerender){
  var item=state.sourceIngest.result&&state.sourceIngest.result.candidates&&state.sourceIngest.result.candidates[index];
  if(!item)return;
  state.sourceIngest.selected=index;
  state.sourceDraft={
    title:mhHumanize(item.qualified_name||item.name),
    statement:state.sourceDraft.statement||"",
    imports:(state.sourceIngest.result.imports||["Mathlib"]).join("\n"),
    declaration:item.declaration||"",
    proof:item.proof||""
  };
  state.sourceRegister={loading:false,error:"",success:null};
  state.sourceCheck={running:false,result:null,error:""};
  if(rerender!==false)render();
}
async function mhIngestFile(file){
  if(!file)return;
  if(file.size>520000){state.sourceIngest={loading:false,error:"That file is too large for the current web importer.",result:null,selected:-1};render();return;}
  await mhIngest({filename:file.name,source_text:await file.text()});
}
async function mhIngest(payload){
  state.sourceCheck={running:false,result:null,error:""};
  state.sourceIngest={loading:true,error:"",result:null,selected:-1};render();
  try{
    var result=await mhPost("/api/source-ingest",payload);
    state.sourceIngest={loading:false,error:"",result:result,selected:-1};
    if(result.candidates&&result.candidates.length)mhApplyCandidate(0,false);
  }catch(error){
    state.sourceIngest={loading:false,error:error.message||String(error),result:null,selected:-1};
  }
  render();
}
async function mhCheck(){
  if(!state.runtime.ready)return;
  state.sourceCheck={running:true,result:null,error:""};render();
  try{
    var declaration=state.sourceDraft.declaration.trim();
    var match=declaration.match(/(?:theorem|lemma|def)\s+([^\s:(]+)/);
    if(!match)throw new Error("The Lean declaration name could not be determined.");
    var result=await mhPost("/api/lean/check",{
      imports:state.sourceDraft.imports.split(/\n+/).map(function(x){return x.trim();}).filter(Boolean),
      declaration:declaration,
      proof:state.sourceDraft.proof.trim(),
      theorem_name:match[1],
      audit:true
    });
    state.sourceCheck={running:false,result:result,error:""};
  }catch(error){
    state.sourceCheck={running:false,result:null,error:error.message||String(error)};
  }
  render();
}

async function mhRegister(event){
  event.preventDefault();
  state.sourceRegister={loading:true,error:"",success:null};render();
  try{
    var declaration=state.sourceDraft.declaration.trim();
    var match=declaration.match(/(?:theorem|lemma|def)\s+([^\s:(]+)/);
    if(!match)throw new Error("The Lean declaration name could not be determined.");
    if(!state.sourceDraft.title.trim()||!state.sourceDraft.statement.trim())throw new Error("Add a readable title and plain-language statement before registering.");
    var payload={
      title:state.sourceDraft.title.trim(),
      statement:state.sourceDraft.statement.trim(),
      formal_declaration:declaration,
      imports:state.sourceDraft.imports.split(/\n+/).map(function(x){return x.trim();}).filter(Boolean),
      proof_source:state.sourceDraft.proof.trim(),
      lean_name:match[1],
      build:true
    };
    var created=await mhPost("/api/claims",payload);
    var registrationMessage=created.created_build&&created.created_build.status?
      ("Build status: "+created.created_build.status+"."):
      (created.build_error?("Stored, but the build did not complete: "+created.build_error):"The statement is stored. No proof build was created.");
    state.sourceRegister={loading:false,error:"",success:registrationMessage};
    if(typeof loadRegistryClaims==="function")await loadRegistryClaims();
    var claimId=created.public_id||created.claim_id;
    if(claimId){state.selected=claimId;state.view="claim";}
    render();
  }catch(error){
    state.sourceRegister={loading:false,error:error.message||String(error),success:null};render();
  }
}

var mathHubProductBaseBind=bind;
bind=function(){
  mathHubProductBaseBind();
  document.querySelectorAll("[data-library-browser]").forEach(function(button){
    button.onclick=function(){if(typeof openLibraryBrowser==="function")openLibraryBrowser();};
  });
  var file=document.querySelector("#mathhub-source-file");
  if(file)file.addEventListener("change",async function(event){
    try{await mhIngestFile(event.currentTarget.files&&event.currentTarget.files[0]);}
    catch(error){state.sourceIngest={loading:false,error:error.message||String(error),result:null,selected:-1};render();}
  });
  var dropzone=document.querySelector(".mathhub-dropzone");
  if(dropzone){
    dropzone.addEventListener("dragover",function(event){event.preventDefault();dropzone.classList.add("dragging");});
    dropzone.addEventListener("dragleave",function(){dropzone.classList.remove("dragging");});
    dropzone.addEventListener("drop",async function(event){
      event.preventDefault();dropzone.classList.remove("dragging");
      try{await mhIngestFile(event.dataTransfer&&event.dataTransfer.files&&event.dataTransfer.files[0]);}
      catch(error){state.sourceIngest={loading:false,error:error.message||String(error),result:null,selected:-1};render();}
    });
  }
  var urlForm=document.querySelector("#mathhub-source-url-form");
  if(urlForm)urlForm.addEventListener("submit",function(event){
    event.preventDefault();var input=document.querySelector("#mathhub-source-url");var url=input&&input.value.trim();if(url)void mhIngest({source_url:url});
  });
  document.querySelectorAll("[data-ingest-candidate]").forEach(function(button){
    button.addEventListener("click",function(){mhApplyCandidate(Number(button.dataset.ingestCandidate));});
  });
  [["draft-title","title"],["draft-human","statement"],["draft-imports","imports"],["draft-decl","declaration"],["draft-proof","proof"]].forEach(function(pair){
    var node=document.querySelector("#"+pair[0]);if(node)node.addEventListener("input",function(event){
      state.sourceDraft[pair[1]]=event.currentTarget.value;
      if(pair[1]==="imports"||pair[1]==="declaration"||pair[1]==="proof")state.sourceCheck={running:false,result:null,error:""};
    });
  });
  var check=document.querySelector("#source-draft-test");if(check)check.onclick=function(){void mhCheck();};
  var form=document.querySelector("#source-claim-form");if(form)form.addEventListener("submit",mhRegister);
};

render();
