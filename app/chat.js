/* =========================================================================
   chat.js — renders ONE chat at /chat/<slug>.

   Loads from /api/chat/<slug>/{meta,latest,…} (v2/live mode) and falls
   back to the static data/<slug>.json snapshot when the server isn't
   reachable. Drives the entire chat-page experience: header, stat
   chips, message bubbles, edit diffs, media overview, calendar
   heatmap, voice playback, live WebSocket push.

   Structure (top → bottom of this file):
     1. Helpers       — formatting, locales, tooltip plumbing.
     2. Edit diffs    — LCS word-diff between message versions.
     3. Media state   — TRANSCRIPTS, OCR, MEDIA cache, filters.
     4. Media views   — vCard / file grid / image grid / audio grid.
     5. Avatars       — initials fallback + image avatars.
     6. Heatmap       — GitHub-style activity calendar.
     7. Chat builder  — bubble HTML from a CanonicalMessage list.
     8. Renderer      — full page render(chat).
     9. Live paging   — IntersectionObserver-driven older/newer load.
    10. Heatmap nav   — click-to-jump, day pinning, scroll syncing.
    11. Voice player  — custom audio widget for chat bubbles.
    12. Entry         — picks loadLive() vs. loadStatic() on boot.
   ========================================================================= */
(function(){
"use strict";

// Transparent 1×1 GIF — used as the lazy-load placeholder so the
// browser doesn't paint its default "broken image" frame before the
// real src is wired up.
var PLACEHOLDER="data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

var WD=["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
var MO=["January","February","March","April","May","June","July","August","September","October","November","December"];
var MOS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
var IC={image:'fa-solid fa-image',video:'fa-solid fa-film',audio:'fa-solid fa-microphone',
        file:'fa-solid fa-paperclip',msgs:'fa-solid fa-comment'};
function esc(s){return (s||"").replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function pad(n){return n<10?"0"+n:""+n;}
function D(ts){return new Date(ts*1000);}
function dayShort(d){return MOS[d.getMonth()]+" "+d.getDate()+", "+d.getFullYear();}
function hm(d){return pad(d.getHours())+":"+pad(d.getMinutes());}
function nf(n){return (n||0).toLocaleString("en-US");}
function fmtBytes(b){b=b||0;
  if(b>=1073741824)return (b/1073741824).toFixed(1)+" GB";
  if(b>=1048576)return Math.round(b/1048576)+" MB";
  if(b>=1024)return Math.round(b/1024)+" KB";
  return b+" B";}
function linkify(t){return esc(t).replace(/(https?:\/\/[^\s]+)/g,function(u){return '<a href="'+u+'" target="_blank" class="msglink">'+u+'</a>';});}
// Map a chat's `origin` ("apple"/"whatsapp"/"signal"/"sms") to a
// FontAwesome icon class + display name. Used by the source-badge
// next to chat titles and avatars.
function originInfo(o){
  o=o||"apple";
  var map={
    apple:{icon:"fa-brands fa-apple",name:"iMessage"},
    whatsapp:{icon:"fa-brands fa-whatsapp",name:"WhatsApp"},
    signal:{icon:"fa-brands fa-signal-messenger",name:"Signal"},
    sms:{icon:"fa-solid fa-comment-sms",name:"SMS / Android"}
  };
  return map[o]||map.apple;
}
// Render reaction chips (iMessage "tapbacks", WhatsApp reactions) for
// a message bubble. One chip per distinct emoji; the tooltip lists
// who reacted and when.
function reactionsHTML(m){
  if(!m.reactions || !m.reactions.length) return "";
  var seen={}, chips="";
  m.reactions.forEach(function(r){
    if(seen[r.emoji]) return; seen[r.emoji]=1;   // dedupe by emoji
    var title=r.sender+": "+r.label;
    if(r.ts){ var rd=D(r.ts); title+=" · "+dayShort(rd)+" "+hm(rd); }
    chips+='<span class="react" title="'+esc(title)+'">'+r.emoji+'</span>';
  });
  return chips?('<div class="reactions">'+chips+'</div>'):"";
}
function qparam(k){var m=new RegExp("[?&]"+k+"=([^&]+)").exec(location.search);return m?decodeURIComponent(m[1]):null;}
// Read the chat slug from /chat/<slug>. The slug may contain slashes
// (e.g. `my_mac/bob`), so we strip them per-segment instead of treating
// the whole path as a single component.
function pathSlug(){var m=/^\/chat\/(.+)$/.exec(location.pathname);return m?m[1].split('/').map(decodeURIComponent).join('/'):null;}

// --- Custom tooltip: appears instantly (native title= has a delay) ----------
// Works on ANY element with a title attribute (source badges, heatmap
// cells, stat chips, …). On the first hover the title is moved into
// data-tip so the native (delayed) tooltip is suppressed too.
(function initTooltip(){
  var tip=null;
  function el(){ if(!tip){ tip=document.createElement('div'); tip.id='mv-tip'; document.body.appendChild(tip); } return tip; }
  function tipTextFor(target){
    var t=target.closest('[title],[data-tip]');
    if(!t) return null;
    if(t.hasAttribute('title')){ t.setAttribute('data-tip', t.getAttribute('title')); t.removeAttribute('title'); }
    return {node:t, text:t.getAttribute('data-tip')};
  }
  function place(e){
    var x=e.clientX, y=e.clientY, t=el();
    var w=t.offsetWidth, h=t.offsetHeight, pad=14;
    var left=x+pad, top=y+pad;
    if(left+w>window.innerWidth-6) left=x-w-pad;       // keep inside right edge
    if(top+h>window.innerHeight-6) top=y-h-pad;        // keep inside bottom edge
    if(left<6) left=6; if(top<6) top=6;
    t.style.left=left+'px'; t.style.top=top+'px';
  }
  document.addEventListener('mouseover', function(e){
    var info=tipTextFor(e.target);
    if(!info||!info.text){ return; }
    var t=el(); t.textContent=info.text; t.classList.add('show'); place(e);
  });
  document.addEventListener('mousemove', function(e){
    if(tip && tip.classList.contains('show')){
      // only keep tracking the cursor while still over a tooltip element
      if(e.target.closest('[data-tip]')) place(e); else { tip.classList.remove('show'); }
    }
  });
  document.addEventListener('mouseout', function(e){
    if(tip && (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest('[data-tip]'))){
      tip.classList.remove('show');
    }
  });
})();

// --- Edited messages: word-level diff between two versions -----------------
// Produces HTML where removed words are highlighted red (strikethrough)
// and added words are highlighted green. Tokenization is word-aware
// AND whitespace-aware, so paragraph breaks and indentation survive
// the diff intact.
function tokenize(s){return (s||"").match(/\s+|[^\s]+/g)||[];}
// LCS-based word diff. Returns {oldHTML, newHTML}: the "original"
// version flags REMOVED words in red, the "final" version flags
// ADDED words in green. Both versions remain readable on their own
// (not a single interleaved diff) so the reader sees the message
// before and after the edit clearly.
function diffPair(oldStr,newStr){
  var a=tokenize(oldStr), b=tokenize(newStr);
  var n=a.length, m=b.length;
  var dp=[]; for(var i=0;i<=n;i++){dp.push(new Array(m+1).fill(0));}
  for(var i=n-1;i>=0;i--){for(var j=m-1;j>=0;j--){
    dp[i][j]= a[i]===b[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j],dp[i][j+1]);
  }}
  var oldOut="", newOut="", i=0, j=0, delBuf="", insBuf="";
  function fDel(){ if(!delBuf)return ""; var s='<span class="diff-del">'+linkify(delBuf)+'</span>'; delBuf=""; return s; }
  function fIns(){ if(!insBuf)return ""; var s='<span class="diff-ins">'+linkify(insBuf)+'</span>'; insBuf=""; return s; }
  while(i<n && j<m){
    if(a[i]===b[j]){ oldOut+=fDel()+esc(a[i]); newOut+=fIns()+esc(b[j]); i++; j++; }
    else if(dp[i+1][j]>=dp[i][j+1]){ delBuf+=a[i]; i++; }
    else { insBuf+=b[j]; j++; }
  }
  while(i<n){ delBuf+=a[i]; i++; }
  while(j<m){ insBuf+=b[j]; j++; }
  oldOut+=fDel(); newOut+=fIns();
  return {oldHTML:oldOut, newHTML:newOut};
}
// "Edited" pill + collapsible diff block for an edited message.
// Clicking the pill expands a row with both the ORIGINAL and the FINAL
// version side by side. Returns "" for messages without an edit
// history (the common case).
function editBlockHTML(m){
  if(!m.edits || m.edits.length<2) return "";
  var orig=m.edits[0].text, fin=m.edits[m.edits.length-1].text;
  var nVer=m.edits.length;
  var d=diffPair(orig,fin);
  var info = nVer>2 ? (nVer+" versions") : "show original";
  return '<div class="edit-wrap">'+
    '<button class="edit-toggle" type="button"><i class="fa-solid fa-pen"></i> Edited · '+esc(info)+'</button>'+
    '<div class="edit-detail" hidden>'+
      '<div class="edit-row"><span class="edit-lbl">Original</span><div class="edit-orig">'+d.oldHTML+'</div></div>'+
      '<div class="edit-row"><span class="edit-lbl">Final version</span><div class="edit-fin">'+d.newHTML+'</div></div>'+
    '</div></div>';
}

// --- Media state (lightbox + per-chat media cache + filters) ---------------
var LB=[], lbIndex=0;                          // lightbox sources + cursor
var MEDIA={image:[],video:[],audio:[],other:[]};   // image items also carry .cat: foto/sticker/emoji
var IMG_FILTER={foto:true,sticker:false,emoji:false};  // default: photos only
var IMG_ONLY_SCREENSHOTS=false;                        // refinement: limit to OCR-detected screenshots
var PERSON_FILTER={image:null,video:null,audio:null,other:null};  // null = everyone; else sender name
var TRANSCRIPTS={};                        // media src path → {text, lang}
var OCR={};                                // media src path → {text, lines, is_screenshot}

// Transcript text for a given audio src, or null if none/empty.
function transcriptText(src){
  var t=TRANSCRIPTS[src];
  return (t && t.text && t.text.trim()) ? t.text.trim() : null;
}
// OCR text for a given image src, or null if none/empty.
function ocrText(src){
  var o=OCR[src];
  return (o && o.text && o.text.trim()) ? o.text.trim() : null;
}
// True if the OCR worker tagged this image as a phone-screenshot
// (rather than a regular photo). Used by the "Screenshots" filter.
function isScreenshot(src){
  var o=OCR[src]; return !!(o && o.is_screenshot);
}
// Open the modal that shows audio transcript OR image OCR text in full.
window.showTranscript=function(src,who,when){
  var txt=transcriptText(src) || ocrText(src); if(!txt)return;
  var m=document.getElementById('trmodal');
  document.getElementById('tr-meta').textContent=(who||'')+(when?(' · '+when):'');
  document.getElementById('tr-text').textContent=txt;
  m.classList.add('open');document.body.style.overflow='hidden';
};
window.closeTranscript=function(){document.getElementById('trmodal').classList.remove('open');document.body.style.overflow='';};

// File extension from a path (lowercased, no leading dot, no query).
function extOf(src){var m=/\.([A-Za-z0-9]+)(?:\?.*)?$/.exec(src||"");return m?m[1].toLowerCase():"";}
// Parse a vCard (.vcf) file body into a flat object of the fields we
// actually care about: full name, org, phone numbers, emails, photo,
// Apple-Maps URL → coordinates. Handles line folding and the few
// common escape sequences. Not RFC-complete.
function parseVCard(txt){
  var unfold=txt.replace(/\r\n[ \t]/g,"").replace(/\n[ \t]/g,"");
  var o={tel:[],email:[]};
  unfold.split(/\r?\n/).forEach(function(line){
    var i=line.indexOf(":"); if(i<0)return;
    var key=line.slice(0,i), val=line.slice(i+1).replace(/\\,/g,",").replace(/\\;/g,";").replace(/\\n/gi,"\n").trim();
    var name=key.split(";")[0].toUpperCase();
    if(name==="FN") o.fn=val;
    else if(name==="N" && !o.fn){o.fn=val.split(";").filter(Boolean).reverse().join(" ");}
    else if(name==="ORG") o.org=val.replace(/;+$/,"");
    else if(name==="TEL") o.tel.push(val);
    else if(name==="EMAIL") o.email.push(val);
    else if(name==="URL") o.url=val;
    else if(name==="PHOTO"){var b=key.match(/ENCODING=b|BASE64/i);o.photo=(b?("data:image/jpeg;base64,"+val):val);}
  });
  // Apple Maps share links embed `ll=<lat>,<lng>` in the URL — detect
  // those so the lightbox can render the vCard as a location card
  // with a "Open in Maps" link instead of a contact card.
  var mm=(o.url||"").match(/[?&]ll=([\-0-9.]+),([\-0-9.]+)/);
  if(mm){o.isLocation=true;o.lat=mm[1];o.lng=mm[2];}
  return o;
}
function fileIcon(ext){return {pdf:'fa-file-pdf',vcf:'fa-address-card',watchface:'fa-clock'}[ext]||'fa-file';}
// Fetch a .vcf attachment and render it as a contact card (or a
// location card if parseVCard detected Apple-Maps coordinates).
function renderVCard(src,box){
  box.innerHTML='<div class="lb-file lb-loading"><i class="fa-solid fa-spinner fa-spin"></i></div>';
  fetch(src).then(function(r){return r.text();}).then(function(txt){
    var v=parseVCard(txt);
    var h='';
    if(v.isLocation){
      h='<div class="vcard vcard-loc">'+
        '<div class="vc-ico"><i class="fa-solid fa-location-dot"></i></div>'+
        '<div class="vc-name">'+esc(v.fn||'Standort')+'</div>'+
        '<div class="vc-rows"><div class="vc-row"><i class="fa-solid fa-map-pin"></i> '+esc(v.lat)+', '+esc(v.lng)+'</div></div>'+
        '<a class="vc-btn" href="https://maps.apple.com/?ll='+esc(v.lat)+','+esc(v.lng)+'&q='+esc(v.lat)+','+esc(v.lng)+'" target="_blank"><i class="fa-solid fa-map"></i> Open in Maps</a>'+
        '<a class="vc-btn vc-btn2" href="https://www.openstreetmap.org/?mlat='+esc(v.lat)+'&mlon='+esc(v.lng)+'#map=16/'+esc(v.lat)+'/'+esc(v.lng)+'" target="_blank"><i class="fa-solid fa-map-location-dot"></i> OpenStreetMap</a>'+
        '</div>';
    } else {
      var rows='';
      v.tel.forEach(function(t){rows+='<div class="vc-row"><i class="fa-solid fa-phone"></i> <a href="tel:'+esc(t.replace(/\s/g,''))+'">'+esc(t)+'</a></div>';});
      v.email.forEach(function(e){rows+='<div class="vc-row"><i class="fa-solid fa-envelope"></i> <a href="mailto:'+esc(e)+'">'+esc(e)+'</a></div>';});
      if(v.url && !v.isLocation) rows+='<div class="vc-row"><i class="fa-solid fa-link"></i> <a href="'+esc(v.url)+'" target="_blank">'+esc(v.url)+'</a></div>';
      h='<div class="vcard">'+
        (v.photo?('<img class="vc-photo" src="'+esc(v.photo)+'" alt="">'):('<div class="vc-ico"><i class="fa-solid fa-user"></i></div>'))+
        '<div class="vc-name">'+esc(v.fn||'Kontakt')+'</div>'+
        (v.org?'<div class="vc-org">'+esc(v.org)+'</div>':'')+
        '<div class="vc-rows">'+rows+'</div>'+
        '<a class="vc-btn" href="'+esc(src)+'" download><i class="fa-solid fa-download"></i> .vcf speichern</a>'+
        '</div>';
    }
    box.innerHTML=h;
  }).catch(function(){
    box.innerHTML='<div class="lb-file"><i class="fa-solid fa-triangle-exclamation"></i> Could not load the file. <a href="'+esc(src)+'" download>Download</a></div>';
  });
}
function lbShow(){
  var it=LB[lbIndex]; if(!it)return;
  var box=document.getElementById('lb-content');
  if(it.type==='video'){
    box.innerHTML='<video controls autoplay src="'+it.src+'"></video>';
  } else if(it.type==='file'){
    var ext=it.ext||extOf(it.src);
    if(ext==='pdf'){
      box.innerHTML='<iframe class="lb-pdf" src="'+it.src+'#view=FitH" title="PDF"></iframe>';
    } else if(ext==='vcf'){
      renderVCard(it.src,box);
    } else {
      box.innerHTML='<div class="lb-file"><i class="fa-solid '+fileIcon(ext)+'"></i><div class="lb-fname">'+esc(it.cap||it.src.split('/').pop())+'</div>'+
        '<a class="vc-btn" href="'+esc(it.src)+'" download><i class="fa-solid fa-download"></i> Herunterladen</a></div>';
    }
  } else {
    box.innerHTML='<img src="'+it.src+'">';
  }
  var single=LB.length<=1;
  // Single image/file: hide the prev/next buttons and the "(1/1)" counter
  document.getElementById('lightbox').classList.toggle('single',single);
  document.getElementById('lightbox').classList.toggle('lb-isfile',it.type==='file');
  document.getElementById('lb-cap').textContent=single?(it.cap||''):((it.cap||'')+'   ('+(lbIndex+1)+'/'+LB.length+')');
}
window.lbOpen=function(list,i){LB=list;lbIndex=i;lbShow();document.getElementById('lightbox').classList.add('open');document.body.style.overflow='hidden';};
window.lbClose=function(){var lb=document.getElementById('lightbox');stopMediaIn(lb);lb.classList.remove('open');document.body.style.overflow='';};
window.lbNav=function(d){lbIndex=(lbIndex+d+LB.length)%LB.length;lbShow();};

// Full media list of the chat (fetched from /api/chat/.../media),
// independent of the paginated rendered window. Loaded lazily the
// first time the overlay opens and then cached for the session.
var MEDIA_FULL_LOADED=false;
function buildMediaFromItems(items){
  var M={image:[],video:[],audio:[],other:[]};
  (items||[]).forEach(function(it){
    var d=it.ts?D(it.ts):null;
    var when=d?(dayShort(d)+" "+hm(d)):"";
    if(it.kind==='image'){
      M.image.push({src:it.src,type:'image',cap:it.cap||"",cat:it.cat||'foto',
        sticker:(it.cat==='sticker'||it.cat==='emoji'),portrait:!!it.portrait,
        sender:it.sender,when:when});
    } else if(it.kind==='video'){
      M.video.push({src:it.src,type:'video',cap:it.cap||"",sender:it.sender,when:when});
    } else if(it.kind==='audio'){
      M.audio.push({src:it.src,type:'audio',cap:it.cap||"",sender:it.sender,when:when});
    } else {
      M.other.push({src:it.src,ext:extOf(it.src),sender:it.sender,when:when});
    }
  });
  return M;
}
var MO_TAB=null;   // aktuell offener Medien-Tab (null = Overlay zu)
// The overlay starts flush under the chat header; set its `top` to
// the *actual* measured header height rather than a hard-coded value.
function moPosition(){
  var ov=document.getElementById('mediaoverlay'); if(!ov)return;
  var head=document.querySelector('.header'); var hh=head?head.offsetHeight:64;
  ov.style.top=hh+'px';
}
window.moOpen=function(tab){
  tab=tab||'image';
  var ov=document.getElementById('mediaoverlay');
  // Chip toggling: clicking the same chip again while its tab is
  // already open simply closes the overlay.
  if(MO_TAB===tab && ov.classList.contains('open')){ moClose(); return; }
  moPosition();
  ov.classList.add('open');
  MO_TAB=tab;
  if(MEDIA_FULL_LOADED){ moRender(tab); return; }
  var body=document.getElementById('mo-body');
  if(body) body.innerHTML='<div class="mo-empty">Loading media …</div>';
  api('/api/chat/'+slug+'/media').then(function(d){
    MEDIA=buildMediaFromItems(d.media||[]);
    MEDIA_FULL_LOADED=true;
    if(MO_TAB) moRender(MO_TAB);     // nur rendern, falls noch offen
  }).catch(function(){
    if(MO_TAB) moRender(MO_TAB);
  });
};
function stopMediaIn(el){
  if(!el)return;
  [].forEach.call(el.querySelectorAll('audio,video'),function(a){try{a.pause();a.currentTime=0;}catch(e){}});
}
window.moClose=function(){
  var ov=document.getElementById('mediaoverlay');
  stopMediaIn(ov);                                  // laufende Sprachnachrichten/Videos stoppen
  [].forEach.call(ov.querySelectorAll('.cell.audio'),function(c){c.classList.remove('playing');});
  ov.classList.remove('open');
  MO_TAB=null;
};
function moLabel(t){return {image:'Images',video:'Videos',audio:'Voice notes'}[t]||t;}

window.toggleImgFilter=function(cat){IMG_FILTER[cat]=!IMG_FILTER[cat];moRender('image');};
window.toggleScreenshotFilter=function(){IMG_ONLY_SCREENSHOTS=!IMG_ONLY_SCREENSHOTS;moRender('image');};
window.setPersonFilter=function(tab,name){PERSON_FILTER[tab]=(PERSON_FILTER[tab]===name)?null:name;moRender(tab);};
// "Edited messages" mode: swaps the chat region IN-PLACE between "all
// messages" and "only the edited ones". Same bubble look, diffs
// auto-expanded, heatmap hidden, no overlay, no close button — the
// same toggle reverts. The normal timeline is saved and restored
// byte-for-byte on exit (including scroll position).
var EDIT_MODE={on:false, savedHTML:null, savedScroll:0, savedDayKey:null};

window.toggleEditFilter=function(){
  if(EDIT_MODE.on) exitEditMode(); else enterEditMode();
};

function enterEditMode(){
  var chatEl=document.querySelector('.chat');
  var pill=document.getElementById('editFilterPill');
  if(!chatEl || EDIT_MODE.on) return;
  // Save the current timeline and remember which day is at the top
  // of the viewport (robust against lazy-image height shifts during
  // restore — a pixel-Y would be fragile).
  EDIT_MODE.savedHTML=chatEl.innerHTML;
  EDIT_MODE.savedScroll=window.scrollY;
  EDIT_MODE.savedDayKey=(function(){
    var head=document.querySelector('.header'); var hh=head?head.offsetHeight:64;
    var th=hh+6, best=null, bt=-1e9;
    document.querySelectorAll('.day').forEach(function(d){var r=d.getBoundingClientRect();if(r.top<=th&&r.top>bt){bt=r.top;best=d.getAttribute('data-key');}});
    return best;
  })();
  EDIT_MODE.on=true;
  document.body.classList.add('edit-mode');       // CSS uses this to hide the heatmap
  if(pill) pill.classList.add('on');
  // Stop pagination COMPLETELY: disconnect the IntersectionObservers,
  // otherwise the top sentinel triggers another loadOlder() and the
  // normal timeline ends up overwriting our edit view.
  if(IO_TOP){ IO_TOP.disconnect(); } if(IO_BOTTOM){ IO_BOTTOM.disconnect(); }
  LIVE.suppressOlder=true; LIVE.suppressNewer=true;
  chatEl.innerHTML='<div style="padding:30px;color:#8e8e93">Loading edited messages …</div>';

  function show(msgs){
    var edits=(msgs||[]).filter(function(m){return m.edits && m.edits.length>1;});
    edits.sort(function(a,b){return a.ts-b.ts;});
    if(!edits.length){ chatEl.innerHTML='<div style="padding:30px;color:#8e8e93">No edited messages.</div>'; return; }
    // same bubble-render path as the main chat — buildChatHTML is
    // shared between modes, so the look stays consistent
    MEDIA={image:[],video:[],audio:[],other:[]};
    chatEl.innerHTML=buildChatHTML(edits, 0, LIVE.meta?LIVE.meta.is_group:false).html;
    // expand every edit diff immediately — in this mode the whole
    // point is the diff, so requiring a click would be wasted friction
    chatEl.querySelectorAll('.edit-detail').forEach(function(det){
      det.hidden=false;
      var tg=det.parentNode.querySelector('.edit-toggle'); if(tg) tg.classList.add('open');
    });
    window.scrollTo({top:0, behavior:'auto'});
  }
  if(LIVE.meta){
    api('/api/chat/'+slug+'/edited').then(function(d){ show(d.messages||[]); })
      .catch(function(){ show(LIVE.messages||[]); });
  } else {
    show(window.__staticMessages||[]);
  }
}

function exitEditMode(){
  var chatEl=document.querySelector('.chat');
  var pill=document.getElementById('editFilterPill');
  if(!chatEl || !EDIT_MODE.on) return;
  // Restore the saved timeline byte-for-byte.
  chatEl.innerHTML=EDIT_MODE.savedHTML;
  EDIT_MODE.on=false;
  document.body.classList.remove('edit-mode');
  if(pill) pill.classList.remove('on');
  LIVE.suppressOlder=false; LIVE.suppressNewer=false;
  if(typeof rebuildDays==='function') rebuildDays();
  setupSentinels();   // re-bind sentinels to the restored DOM
  // Scroll back to the saved place — via the day-key (robust against
  // lazy-image height changes), with scrollToDayElementStable doing
  // the small correcting follow-ups until the position settles.
  var key=EDIT_MODE.savedDayKey;
  requestAnimationFrame(function(){
    var t=key && document.querySelector('.day[data-key="'+key+'"]');
    if(t && typeof scrollToDayElementStable==='function') scrollToDayElementStable(t);
    else window.scrollTo({top:EDIT_MODE.savedScroll, behavior:'auto'});
  });
}

// Per-person filter bar shown above each media tab. Identical
// rendering across all four tabs (image / video / audio / other).
function personBar(tab,pool){
  var names=uniqueSenders(pool);
  if(names.length<2)return"";  // single-person chats don't need a filter
  var cur=PERSON_FILTER[tab];
  return '<span class="flabel">From:</span>'+
    '<button class="fbtn '+(cur===null?'on':'')+'" onclick="setPersonFilter(\''+tab+'\',null)"><i class="fa-solid fa-users"></i> All</button>'+
    names.map(function(n){
      var cnt=pool.filter(function(x){return x.sender===n;}).length;
      return '<button class="fbtn '+(cur===n?'on':'')+'" onclick="setPersonFilter(\''+tab+'\',\''+esc(n).replace(/'/g,"")+'\')"><i class="fa-solid fa-user"></i> '+esc(n)+' ('+cnt+')</button>';
    }).join("");
}
function byPerson(tab,arr){var p=PERSON_FILTER[tab];return p===null?arr:arr.filter(function(x){return x.sender===p;});}

// Render the media-overview body for the active tab. Tab selection
// happens through the chips in the chat header (mediaPill);
// moRender() just rebuilds the filter bar + grid for whichever tab
// the user opened.
window.moRender=function(tab){
  MO_TAB=tab;   // remember which tab is open
  var filt=document.getElementById('mo-filters');
  var body=document.getElementById('mo-body');

  if(tab==='image'){
    filt.style.display='flex';
    // Apply category filter first, then derive the person filter from
    // that filtered base (so the "By person" counts match what's
    // actually shown, not the unfiltered superset).
    var catBase=MEDIA.image.filter(function(x){return IMG_FILTER[x.cat||'foto'];});
    if(IMG_ONLY_SCREENSHOTS) catBase=catBase.filter(function(x){return isScreenshot(x.src);});
    var ssBtn='<button class="fbtn'+(IMG_ONLY_SCREENSHOTS?' on':'')+'" onclick="toggleScreenshotFilter()" title="Show only detected screenshots"><i class="fa-solid fa-mobile-screen-button"></i> Screenshots</button>';
    filt.innerHTML='<span class="flabel">Show:</span>'+
      fbtn('foto','fa-solid fa-image','Photos')+
      fbtn('sticker','fa-solid fa-note-sticky','Stickers')+
      fbtn('emoji','fa-solid fa-face-smile','Emojis')+
      '<span class="fsep"></span>'+ssBtn+
      '<span class="fsep"></span>'+personBar('image',catBase);
    renderGrid(body,byPerson('image',catBase),'image');
  } else if(tab==='video'){
    filt.style.display='flex';
    filt.innerHTML=personBar('video',MEDIA.video) || '<span class="flabel">All videos</span>';
    renderGrid(body,byPerson('video',MEDIA.video),'video');
  } else if(tab==='audio'){
    filt.style.display='flex';
    filt.innerHTML=personBar('audio',MEDIA.audio) || '<span class="flabel">All voice notes</span>';
    renderAudioGrid(body,byPerson('audio',MEDIA.audio));
  } else if(tab==='other'){
    filt.style.display='flex';
    filt.innerHTML=personBar('other',MEDIA.other) || '<span class="flabel">All files</span>';
    renderFileGrid(body,byPerson('other',MEDIA.other));
  }
};
// Render the "other" tab — PDFs / vCards / generic files as a tile
// grid. Clickable items open the lightbox; non-viewable ones offer a
// download link instead.
function renderFileGrid(body,items){
  if(!items.length){body.innerHTML='<div class="mo-empty">No files (with the current filter).</div>';return;}
  var labels={pdf:'PDF-Dokument',vcf:'Kontakt / Standort',watchface:'Watch-Zifferblatt'};
  var h='<div class="mo-grid mo-files">';
  items.forEach(function(it,i){
    var ext=it.ext||extOf(it.src);
    var viewable=(ext==='pdf'||ext==='vcf');
    var label=labels[ext]||'File';
    var act=viewable?((ext==='vcf'?'<i class="fa-solid fa-up-right-from-square"></i> Open':'<i class="fa-solid fa-eye"></i> View')):'<i class="fa-solid fa-download"></i> Download';
    h+='<div class="cell file'+(viewable?' viewable':'')+'" data-i="'+i+'">'+
       '<i class="fc-type fa-solid '+fileIcon(ext)+'"></i>'+
       '<div class="ff-name">'+esc(label)+'</div>'+
       '<div class="ff-meta">'+ext.toUpperCase()+(it.when?(' · '+esc(it.when)):'')+'</div>'+
       '<div class="ff-who">'+esc(it.sender||'')+'</div>'+
       '<span class="fc-act">'+act+'</span>'+
       '</div>';
  });
  body.innerHTML=h+'</div>';
  [].forEach.call(body.querySelectorAll('.cell.file'),function(c){
    var it=items[parseInt(c.getAttribute('data-i'))];
    var ext=it.ext||extOf(it.src);
    c.addEventListener('click',function(){
      if(ext==='pdf'||ext==='vcf'){ lbOpen([{src:it.src,type:'file',ext:ext,cap:it.src.split('/').pop()}],0); }
      else { var a=document.createElement('a');a.href=it.src;a.download='';document.body.appendChild(a);a.click();a.remove(); }
    });
  });
}
function fbtn(cat,icon,label){return '<button class="fbtn '+(IMG_FILTER[cat]?'on':'')+'" onclick="toggleImgFilter(\''+cat+'\')"><i class="'+icon+'"></i> '+label+'</button>';}
function uniqueSenders(arr){var s=[];arr.forEach(function(x){if(x.sender&&s.indexOf(x.sender)<0)s.push(x.sender);});return s;}

function renderGrid(body,items,tab){
  if(!items.length){body.innerHTML='<div class="mo-empty">No '+moLabel(tab).toLowerCase()+' (with active filter).</div>';return;}
  var h='<div class="mo-grid">';
  items.forEach(function(it,i){
    var m=it.type==='video'?'<video src="'+it.src+'" preload="metadata"></video><span class="mt"><i class="fa-solid fa-film"></i></span>':'<img class="lazyload" src="'+PLACEHOLDER+'" data-src="'+it.src+'" alt="">';
    // OCR badge for images that have recognized text.
    var ocrBtn='';
    if(tab==='image' && ocrText(it.src)){
      ocrBtn='<button class="ocrbtn tile" title="Show text in image" onclick="event.stopPropagation();showTranscript(\''+it.src+'\',\''+esc(it.sender||'').replace(/'/g,"")+'\',\''+esc(it.when||'')+'\')"><i class="fa-solid fa-file-lines"></i></button>';
    }
    h+='<div class="cell" data-i="'+i+'">'+m+ocrBtn+'</div>';
  });
  body.innerHTML=h+'</div>';
  [].forEach.call(body.querySelectorAll('.cell'),function(c){
    c.addEventListener('click',function(){lbOpen(items,parseInt(c.getAttribute('data-i')));});
  });
}
function fmtDur(sec){
  if(!isFinite(sec)||sec<0)sec=0;
  var m=Math.floor(sec/60),s=Math.floor(sec%60);
  return m+":"+(s<10?"0":"")+s;
}
function renderAudioGrid(body,items){
  if(!items.length){body.innerHTML='<div class="mo-empty">No voice notes (with active filter).</div>';return;}
  // Sorted by date — items already arrive chronologically.
  var h='<div class="mo-grid">';
  items.forEach(function(it,i){
    var tr=transcriptText(it.src);
    var trBtn=tr?('<button class="trbtn tile" title="Show transcript" onclick="event.stopPropagation();showTranscript(\''+it.src+'\',\''+esc(it.sender||'').replace(/'/g,"")+'\',\''+esc(it.when||'')+'\')"><i class="fa-solid fa-quote-right"></i></button>'):'';
    h+='<div class="cell audio" data-src="'+it.src+'">'+trBtn+
       '<i class="ai fa-solid fa-microphone-lines"></i>'+
       '<div class="who">'+esc(it.sender||'')+'</div>'+
       '<div class="when">'+esc(it.when||'')+'</div>'+
       '<div class="progwrap">'+
         '<div class="progbar"><div class="fill"></div><div class="knob"></div></div>'+
         '<div class="progtime">0:00 / 0:00</div>'+
       '</div>'+
       '<audio preload="none" src="'+it.src+'" style="display:none"></audio></div>';
  });
  body.innerHTML=h+'</div>';

  [].forEach.call(body.querySelectorAll('.cell.audio'),function(c){
    var au=c.querySelector('audio');
    var fill=c.querySelector('.fill'), knob=c.querySelector('.knob');
    var ptime=c.querySelector('.progtime'), bar=c.querySelector('.progbar');

    function paint(){
      var d=au.duration||0, t=au.currentTime||0;
      var pct=d?(t/d*100):0;
      fill.style.width=pct+"%"; knob.style.left=pct+"%";
      ptime.textContent=fmtDur(t)+" / "+fmtDur(d);
    }
    au.addEventListener('loadedmetadata',paint);
    au.addEventListener('timeupdate',paint);
    au.addEventListener('ended',function(){c.classList.remove('playing');paint();});

    // Click on the tile = play/pause (but not when interacting with the progress bar).
    c.addEventListener('click',function(e){
      if(e.target.closest('.progbar'))return;
      [].forEach.call(body.querySelectorAll('audio'),function(a){if(a!==au){a.pause();}});
      [].forEach.call(body.querySelectorAll('.cell.audio'),function(x){if(x!==c)x.classList.remove('playing');});
      if(au.paused){au.play();c.classList.add('playing','played');}else{au.pause();c.classList.remove('playing');}
    });

    // Seek by clicking or dragging on the progress bar.
    function seekTo(clientX){
      var r=bar.getBoundingClientRect();
      var ratio=Math.min(1,Math.max(0,(clientX-r.left)/r.width));
      if(au.duration){au.currentTime=ratio*au.duration;paint();}
    }
    bar.addEventListener('click',function(e){e.stopPropagation();seekTo(e.clientX);});
    var dragging=false;
    bar.addEventListener('mousedown',function(e){e.stopPropagation();dragging=true;seekTo(e.clientX);});
    window.addEventListener('mousemove',function(e){if(dragging)seekTo(e.clientX);});
    window.addEventListener('mouseup',function(){dragging=false;});
  });
}

function avatarInitials(title){return title.split(/\s+/).slice(0,2).map(function(w){return w[0];}).join("").toUpperCase();}

// Avatar image with initials fallback (used for chat header + sender bubbles).
function avatarImg(src, title, cls){
  var ini=esc(avatarInitials(title));
  var k=cls||'avatar';
  if(src){
    var url=mvUrl('/'+src);
    return '<div class="'+k+' av-img-wrap">'+
      '<img class="av-img" src="'+esc(url)+'" alt="'+ini+'" '+
        'onerror="this.style.display=\'none\';this.nextSibling.style.display=\'flex\';"/>'+
      '<span class="av-fallback" style="display:none;">'+ini+'</span>'+
    '</div>';
  }
  return '<div class="'+k+'">'+ini+'</div>';
}

function chatHeaderAvatar(chat){
  return avatarImg(chat.chat_avatar, chat.title || '?', 'avatar');
}

// ---- Calendar heatmap (GitHub-contributions style) ------------------------
// Renders one cell per day from chat.stats.first to chat.stats.last,
// coloured by message volume that day. Layout is portrait: weeks run
// vertically, each row carries an optional month label in the gutter.
function dateKey(d){return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());}
function buildHeatmap(chat, perDayOverride){
  // Count messages per day. In v2/live mode perDayOverride is the
  // COMPLETE per-day stats (every day of the chat's lifetime); in
  // static mode we count from the loaded message window.
  var perDay={}, maxN=0;
  if(perDayOverride){
    perDay=perDayOverride;
    for(var k0 in perDay){ if(perDay[k0]>maxN)maxN=perDay[k0]; }
  } else {
    chat.messages.forEach(function(m){
      if(m.t!=='msg')return;
      var k=dateKey(D(m.ts));
      perDay[k]=(perDay[k]||0)+1;
      if(perDay[k]>maxN)maxN=perDay[k];
    });
  }
  if(!chat.stats.first||!chat.stats.last)return "";

  // Date range: first → last message. No more back-padding to a Monday;
  // instead, every month starts with empty leading cells so the Mon–Sun
  // columns still align across months (day 1 lands under its real
  // weekday column, not under "Monday" by force).
  var start=new Date(D(chat.stats.first)); start.setHours(0,0,0,0);
  var end=new Date(D(chat.stats.last)); end.setHours(0,0,0,0);

  function level(n){
    if(!n)return 0;
    if(maxN<=4) return Math.min(5,n);           // low-traffic chats: 1 step per message
    var r=n/maxN;
    if(r<=0.10)return 1; if(r<=0.25)return 2; if(r<=0.50)return 3; if(r<=0.75)return 4; return 5;
  }

  // Portrait layout, single CSS grid: one row per week, each row is
  // [month-label | 7 day cells]. The label sharing the same row as
  // its cells guarantees alignment without measuring anything in JS.
  // Year boundaries get a full-width separator with the year number;
  // month boundaries get a thin divider line.
  var rows=[], curMonth=-1, curYear=-1;
  var cur=new Date(start);
  var week=null, labelNextWeek=true;   // first week of a month carries the month label

  function flushWeek(){
    if(week && week.cells.length){
      rows.push('<div class="hm-week"><div class="hm-mlabel">'+week.label+'</div>'+week.cells.join("")+'</div>');
    }
    week=null;
  }
  while(cur<=end){
    var dow=(cur.getDay()+6)%7;
    var mo=cur.getMonth(), yr=cur.getFullYear();

    // Close the running week at week boundaries OR when crossing
    // month/year (we never let a week span months in the visual grid)
    if(week && (dow===0 || mo!==curMonth || yr!==curYear)) flushWeek();

    // Year boundary: emit a full-width header row.
    if(yr!==curYear){
      flushWeek();
      rows.push('<div class="hm-year"><span>'+yr+'</span></div>');
      curYear=yr; curMonth=-1; labelNextWeek=true;
    }
    // Month boundary within a year: emit a thin divider row.
    if(mo!==curMonth){
      if(curMonth!==-1) rows.push('<div class="hm-msep"></div>');
      curMonth=mo; labelNextWeek=true;
    }
    // Start a new week. Only the first week of a month/year gets the
    // month label in the gutter — subsequent weeks have an empty gutter.
    if(!week){
      week={label: labelNextWeek?MOS[mo]:"", cells:[]};
      labelNextWeek=false;
      // First week of a month: pre-fill empty padding cells so all
      // Mon–Sun columns align across months. The 1st of the month
      // lands under its actual weekday column, not forced to Monday.
      for(var pad_i=0; pad_i<dow; pad_i++){
        week.cells.push('<div class="hm-cell hm-pad"></div>');
      }
    }

    var k=dateKey(cur);
    var n=perDay[k]||0, lv=level(n), has=n>0;
    var title=fmtFullShort(cur)+(n?(' · '+n+' message'+(n>1?'s':'')):' · none');
    week.cells.push('<div class="hm-cell l'+lv+(has?' has':'')+'" '+
               (has?('data-day="'+k+'" '):'')+'title="'+esc(title)+'"></div>');
    cur.setDate(cur.getDate()+1);
  }
  flushWeek();

  return '<div class="heatmap" id="heatmap">'+
    '<div class="hm-scroll"><div class="hm-inner">'+rows.join("")+'</div></div></div>';
}
var MONTHS_HM=null;
function fmtFullShort(d){return WD[d.getDay()]+", "+d.getDate()+". "+MOS[d.getMonth()]+" "+d.getFullYear();}

// Build the HTML for a list of messages (day groups + bubbles).
// Used by both render() (first paint) and v2 paging (prepend/append)
// so there's exactly ONE render path. `dayCountStart` controls the
// a/b alternating tone so prepended days continue the existing
// stripe pattern rather than restarting it.
function buildChatHTML(messages, dayCountStart, isGroup){
  var html=[];
  var lastDay=null, dayBucket=null, dayCount=dayCountStart||0, prevSender=null;
  function side(m){return m.me?"me":"them";}
  function flushOpen(){ if(dayBucket){html.push(dayBucket.join(""));html.push('</div></div>');} }

  messages.forEach(function(m){
    var d=D(m.ts);
    var dayKey=d.getFullYear()+"-"+d.getMonth()+"-"+d.getDate();
    if(dayKey!==lastDay){
      flushOpen();
      lastDay=dayKey; prevSender=null;
      var tone=(dayCount%2===0)?"a":"b"; dayCount++;
      html.push('<div class="day '+tone+'" data-key="'+dateKey(d)+'" data-date="'+esc(dayShort(d))+'" data-wd="'+WD[d.getDay()]+'">');
      html.push('<div class="dayhead"><span class="wd">'+WD[d.getDay()]+'</span><span class="dt">'+d.getDate()+". "+MO[d.getMonth()]+" "+d.getFullYear()+'</span></div>');
      html.push('<div class="dayinner">');
      dayBucket=[];
    }
    if(isGroup && !m.me && m.sender!==prevSender){
      dayBucket.push('<div class="sender">'+esc(m.sender)+'</div>');
    }
    prevSender=m.me?null:m.sender;

    var cap=esc(m.sender)+" · "+dayShort(d)+" "+hm(d);
    var imgs=(m.media||[]).filter(function(x){return x.kind==='image';});
    var vids=(m.media||[]).filter(function(x){return x.kind==='video';});
    var auds=(m.media||[]).filter(function(x){return x.kind==='audio';});
    var oths=(m.media||[]).filter(function(x){return x.kind==='other';});

    var imgItems=imgs.map(function(x){var it={src:x.src,type:'image',cap:cap,cat:x.cat||'foto',sticker:(x.cat==='sticker'||x.cat==='emoji'),portrait:!!x.portrait,sender:m.sender};MEDIA.image.push(it);return it;});
    vids.forEach(function(x){MEDIA.video.push({src:x.src,type:'video',cap:cap,sender:m.sender});});
    auds.forEach(function(x){MEDIA.audio.push({src:x.src,type:'audio',cap:cap,sender:m.sender,when:dayShort(d)+" "+hm(d)});});
    oths.forEach(function(x){MEDIA.other.push({src:x.src,ext:extOf(x.src),sender:m.sender,when:dayShort(d)+" "+hm(d)});});

    if(imgItems.length){
      // OCR "Text" badge: appears whenever the OCR worker produced
      // recognizable text for the image. Clicking it opens the same
      // transcript modal used for voice notes.
      function ocrBadge(it){
        return ocrText(it.src)?('<button class="ocrbtn" title="Show text in image" onclick="event.stopPropagation();showTranscript(\''+it.src+'\',\''+esc(m.sender).replace(/'/g,"")+'\',\''+dayShort(d)+' '+hm(d)+'\')"><i class="fa-solid fa-file-lines"></i></button>'):'';
      }
      if(imgItems.length>2){
        var n=imgItems.length;
        var cls=n===3?"n3":n===4?"n4":n===5?"n5":n===6?"n6":"nmany";
        var shown=n>6?imgItems.slice(0,6):imgItems;
        var cells=shown.map(function(it,j){
          var more=(n>6&&j===5)?'<div class="more">+'+(n-6)+'</div>':'';
          return '<div class="gi"><img class="lazyload" src="'+PLACEHOLDER+'" data-src="'+it.src+'" alt="">'+ocrBadge(it)+more+'</div>';
        }).join("");
        var gal='<div class="gallery '+cls+'" data-imgs=\''+esc(JSON.stringify(imgItems.map(function(x){return {src:x.src,type:'image',cap:x.cap};})))+'\'>'+cells+'</div>';
        dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble media">'+gal+'</div></div>');
      }else{
        imgItems.forEach(function(it){
          var scls=it.sticker?'sticker':'';
          var pcls=it.portrait?' portrait':'';
          dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble media'+pcls+'"><img class="lb lazyload '+scls+'" data-single=\''+esc(JSON.stringify({src:it.src,type:'image',cap:it.cap}))+'\' src="'+PLACEHOLDER+'" data-src="'+it.src+'" alt="">'+ocrBadge(it)+'</div></div>');
        });
      }
    }
    vids.forEach(function(x){
      var pcls=x.portrait?' portrait':'';
      dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble media'+pcls+'"><video class="lb" data-single=\''+esc(JSON.stringify({src:x.src,type:'video',cap:cap}))+'\' controls preload="metadata" src="'+x.src+'"></video></div></div>');
    });
    auds.forEach(function(x){
      var tr=transcriptText(x.src);
      var trBtn=tr?('<button class="trbtn" title="Transkript anzeigen" onclick="showTranscript(\''+x.src+'\',\''+esc(m.sender).replace(/'/g,"")+'\',\''+dayShort(d)+' '+hm(d)+'\')"><i class="fa-solid fa-quote-right"></i></button>'):'';
      dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble voice">'+
        '<div class="voicemsg">'+
          '<span class="voiceicon"><i class="fa-solid fa-microphone"></i></span>'+
          '<div class="voicewrap">'+
            '<div class="voicelabel">Voice note'+trBtn+'</div>'+
            '<div class="vplayer">'+
              '<button class="vp-play" aria-label="Abspielen"><i class="fa-solid fa-play"></i></button>'+
              '<div class="vp-bar"><div class="vp-fill"></div><div class="vp-knob"></div></div>'+
              '<span class="vp-time">0:00</span>'+
              '<audio preload="none" src="'+x.src+'"></audio>'+
            '</div>'+
          '</div>'+
        '</div></div></div>');
    });
    oths.forEach(function(x){
      var ext=extOf(x.src);
      var fname=x.src.split('/').pop();
      var label={pdf:'PDF document',vcf:'Contact / location',watchface:'Watch face'}[ext]||'File';
      if(ext==='pdf'||ext==='vcf'){
        var single=JSON.stringify({src:x.src,type:'file',ext:ext,cap:fname});
        var actLbl=(ext==='vcf')?'Open':'View';
        dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span>'+
          '<div class="bubble media"><span class="filechip viewable" data-single=\''+esc(single)+'\' title="'+actLbl+'">'+
          '<i class="fc-type fa-solid '+fileIcon(ext)+'"></i>'+
          '<span class="fc-info"><span class="fc-name">'+esc(label)+'</span>'+
          '<span class="fc-ext">'+ext.toUpperCase()+'</span></span>'+
          '<span class="fc-act"><i class="fa-solid '+(ext==='vcf'?'fa-up-right-from-square':'fa-eye')+'"></i> '+actLbl+'</span>'+
          '</span></div></div>');
      } else {
        dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span>'+
          '<div class="bubble media"><a href="'+x.src+'" target="_blank" download class="filechip" title="Herunterladen">'+
          '<i class="fc-type fa-solid '+fileIcon(ext)+'"></i>'+
          '<span class="fc-info"><span class="fc-name">'+esc(label)+'</span>'+
          '<span class="fc-ext">'+(ext?ext.toUpperCase():'DATEI')+'</span></span>'+
          '<span class="fc-act"><i class="fa-solid fa-download"></i> Download</span>'+
          '</span></a></div></div>');
      }
    });
    (m.apps||[]).forEach(function(a){
      dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble media"><span class="appchip">'+esc(a)+'</span></div></div>');
    });
    if(m.text){
      var eb = editBlockHTML(m);
      var recls = m.retracted ? ' retracted' : '';
      var editcls = (m.edits && m.edits.length>1) ? ' has-edit' : '';
      var retag = m.retracted ? '<span class="retract-tag"><i class="fa-solid fa-rotate-left"></i> Retracted</span>' : '';
      dayBucket.push('<div class="row '+side(m)+editcls+'"><span class="time">'+hm(d)+'</span><div class="bubble'+recls+'">'+linkify(m.text)+retag+eb+'</div></div>');
    } else if(m.retracted){
      dayBucket.push('<div class="row '+side(m)+'"><span class="time">'+hm(d)+'</span><div class="bubble retracted"><span class="retract-tag"><i class="fa-solid fa-rotate-left"></i> Retracted message</span></div></div>');
    }
    var rx=reactionsHTML(m);
    if(rx && dayBucket.length){
      var last=dayBucket[dayBucket.length-1];
      last=last.replace(/<div class="bubble( media)?">/, '<div class="bubble$1 has-react">');
      last=last.replace(/(<\/div><\/div>)\s*$/, rx+'$1');
      dayBucket[dayBucket.length-1]=last;
    }
  });
  flushOpen();
  return {html:html.join(""), dayCount:dayCount};
}

function render(chat){
  document.title="Chat: "+chat.title;
  var me=chat.me_name||"Levi";
  var s=chat.stats;
  var period="";
  if(s.first&&s.last){var d1=D(s.first),d2=D(s.last);var days=Math.round((d2-d1)/86400000)+1;period=dayShort(d1)+" – "+dayShort(d2)+" · "+days+" days";}

  function mediaPill(typ,icon,label){
    var m=s.media[typ].me,t=s.media[typ].them; if(!m&&!t)return"";
    var clickable=(typ==='image'||typ==='video'||typ==='audio'||typ==='other');
    var onc=clickable?' class="pill btn" onclick="moOpen(\''+typ+'\')"':' class="pill"';
    var sz=(s.bytes&&s.bytes[typ])?' <em class="sz">'+fmtBytes(s.bytes[typ])+'</em>':'';
    return '<span'+onc+'><i class="'+icon+'"></i> '+esc(label)+': '+
           '<b class="updown"><span class="s">'+m+'<i class="fa-solid fa-arrow-up" style="font-size:9px"></i></span>'+
           '<span class="r">'+t+'<i class="fa-solid fa-arrow-down" style="font-size:9px"></i></span></b>'+sz+'</span>';
  }
  var themLabel=chat.is_group?"Others":esc(chat.title.split(" ")[0]);
  var sizeChip=s.bytes_total?'<span class="pill" title="Web media + original images"><i class="fa-solid fa-database"></i> <b>'+fmtBytes(s.bytes_total)+'</b>'+(s.bytes_orig?' <span class="sz">+ '+fmtBytes(s.bytes_orig)+' orig.</span>':'')+'</span>':'';
  // "Edited" filter pill — only show it when at least one edited
  // message exists. In v2/live mode we use the true total from
  // /meta; in static mode we count from the loaded message window.
  var nEdited=(LIVE.meta && typeof LIVE.editedTotal==='number')
      ? LIVE.editedTotal
      : chat.messages.reduce(function(a,m){return a+((m.edits&&m.edits.length>1)?1:0);},0);
  var editPill=nEdited?'<span class="pill btn edit-filter" id="editFilterPill" onclick="toggleEditFilter()" title="Show only edited messages"><i class="fa-solid fa-pen"></i> Edited: <b>'+nf(nEdited)+'</b></span>':'';

  var header=''+
    '<div class="header"><div class="hcenter">'+
      '<a class="back" href="/" title="Index"><i class="fa-solid fa-chevron-left"></i></a>'+
      '<div class="avwrap">'+chatHeaderAvatar(chat)+
        '<span class="origin-badge origin-'+(chat.origin||'apple')+'" title="'+originInfo(chat.origin).name+'"><i class="'+originInfo(chat.origin).icon+'"></i></span></div>'+
      '<div class="hinfo"><div class="title">'+esc(chat.title)+(chat.is_group?' <span class="badge-group"><i class="fa-solid fa-user-group"></i> GROUP</span>':'')+'</div>'+
        '<div class="subtitle">'+esc(chat.subtitle)+'</div>'+
        '<div class="period"><i class="fa-regular fa-calendar"></i> '+period+'</div></div>'+
    '</div>'+
    // Zeile 1: Info-Chips
    '<div class="statline">'+
      '<span class="pill"><i class="'+IC.msgs+'"></i> <b>'+nf(s.total)+'</b> messages</span>'+
      '<span class="pill">'+esc(me)+': <b class="s">'+nf(s.me)+'</b></span>'+
      '<span class="pill">'+themLabel+': <b>'+nf(s.them)+'</b></span>'+
      sizeChip+
      editPill+
    '</div>'+
    // Zeile 2: Medientyp-Chips (klickbar)
    '<div class="medialine">'+
      mediaPill('image',IC.image,'Images')+mediaPill('video',IC.video,'Videos')+
      mediaPill('audio',IC.audio,'Voice')+mediaPill('other',IC.file,'Files')+
    '</div></div>';

  var built=buildChatHTML(chat.messages, 0, chat.is_group);
  var html=[built.html];

  var overlay=''+
    '<div id="mediaoverlay" class="mediaoverlay">'+
      '<div id="mo-filters" class="mo-filters" style="display:none"></div>'+
      '<div id="mo-body" class="mo-body"></div></div>'+
    '<div id="lightbox" class="lightbox"><button class="closebtn lb-close" onclick="lbClose()" title="Close"><i class="fa-solid fa-xmark"></i></button>'+
      '<button class="lb-nav lb-prev" onclick="lbNav(-1)"><i class="fa-solid fa-chevron-left"></i></button><div id="lb-content"></div>'+
      '<button class="lb-nav lb-next" onclick="lbNav(1)"><i class="fa-solid fa-chevron-right"></i></button><div id="lb-cap" class="lb-cap"></div></div>'+
    // Transkript-Modal
    '<div id="trmodal" class="trmodal" onclick="if(event.target===this)closeTranscript()">'+
      '<div class="trbox"><div class="tr-top"><div class="tr-title"><i class="fa-solid fa-quote-right"></i> Transkript</div>'+
        '<button class="closebtn" onclick="closeTranscript()" title="Close"><i class="fa-solid fa-xmark"></i></button></div>'+
        '<div id="tr-meta" class="tr-meta"></div><div id="tr-text" class="tr-text"></div>'+
        '<div class="tr-foot"><i class="fa-solid fa-wand-magic-sparkles"></i> Automatisch transkribiert (Whisper) – kann Fehler enthalten.</div>'+
      '</div></div>';

  document.body.innerHTML=
    header+
    // Right-side panel: date navigation + heatmap as ONE block.
    '<div id="daypanel" class="daypanel">'+
      '<div id="dateflag" class="dateflag">'+
        '<button class="df-nav" id="df-prev" title="Previous day" onclick="jumpDay(-1)"><i class="fa-solid fa-chevron-left"></i></button>'+
        '<div class="df-label" id="dateflag-label"></div>'+
        '<button class="df-nav" id="df-next" title="Next day" onclick="jumpDay(1)"><i class="fa-solid fa-chevron-right"></i></button>'+
      '</div>'+
      '<button class="df-toggle" id="df-toggle" title="Heatmap ein-/ausklappen" onclick="toggleHeatmap()"><i class="fa-solid fa-chevron-up"></i></button>'+
      buildHeatmap(chat, (LIVE&&LIVE.days)?LIVE.days:null)+
    '</div>'+
    '<div class="chat">'+html.join("")+'</div>'+
    '<div class="footer">Message Visualizer'+(chat.device?' · '+esc(chat.device):'')+'</div>'+
    '<button id="totop" class="totop" title="Back to top" onclick="window.scrollTo({top:0,behavior:\'smooth\'})"><i class="fa-solid fa-arrow-up"></i></button>'+
    overlay;

  initInteractions();
}

// ===========================================================================
//  v2 scroll engine (paginated, bidirectional)
//  - IntersectionObserver sentinels at the top and bottom trigger
//    older/newer fetches when they come into view (robust against
//    lazy-image height shifts; no scroll-event spam).
//  - loadOlder() prepends older messages, loadNewer() appends newer.
//  - A scroll anchor keeps the visually-stable position on prepend.
// ===========================================================================
var IO_TOP=null, IO_BOTTOM=null;

function initLivePaging(){
  setupSentinels();
  connectLive();
  // Save the current position ONLY on real user-driven scroll
  // (wheel / touchmove), debounced. Programmatic scrolling (initial
  // load, jumpToDayKey, loadOlder) deliberately doesn't trigger it,
  // otherwise the saved key would drift on every pagination event.
  function markEngaged(){ LIVE.userEngaged=true; }
  window.addEventListener('wheel', markEngaged, {passive:true});
  window.addEventListener('touchmove', markEngaged, {passive:true});
  var saveTick=null;
  window.addEventListener('scroll', function(){
    if(!LIVE.userEngaged) return;          // skip until the user actually drove a scroll
    if(saveTick) return;
    saveTick=setTimeout(function(){
      saveTick=null;
      // Pick whichever .day is currently at the top of the viewport
      // (largest top ≤ headerHeight + 6px) and persist its data-key.
      var head=document.querySelector('.header'); var headH=head?head.offsetHeight:64;
      var th=headH+6, best=null, bt=-1e9;
      document.querySelectorAll('.day').forEach(function(d){var r=d.getBoundingClientRect();if(r.top<=th&&r.top>bt){bt=r.top;best=d.getAttribute('data-key');}});
      if(best) saveCurrentDay(slug, best);
    }, 400);
  }, {passive:true});
}

// Sentinels are invisible 1px markers at the top and bottom of the
// .chat container. When an IntersectionObserver fires on one, we
// trigger the corresponding loadOlder/loadNewer fetch.
function setupSentinels(){
  var chatEl=document.querySelector('.chat');
  if(!chatEl) return;
  // Clear any existing observers (a previous mode/page may have left them).
  if(IO_TOP){ IO_TOP.disconnect(); IO_TOP=null; }
  if(IO_BOTTOM){ IO_BOTTOM.disconnect(); IO_BOTTOM=null; }
  var topS=chatEl.querySelector('#sentinel-top')||mkSentinel('sentinel-top');
  var botS=chatEl.querySelector('#sentinel-bottom')||mkSentinel('sentinel-bottom');
  if(topS.parentNode!==chatEl || chatEl.firstChild!==topS) chatEl.insertBefore(topS, chatEl.firstChild);
  if(botS.parentNode!==chatEl || chatEl.lastChild!==botS) chatEl.appendChild(botS);
  // rootMargin 800px: pre-load ~800px before the user actually reaches
  // the sentinel, so paging feels seamless rather than gappy.
  IO_TOP=new IntersectionObserver(function(es){
    if(es[0].isIntersecting) loadOlder();
  }, {rootMargin:'800px 0px 0px 0px'});
  IO_BOTTOM=new IntersectionObserver(function(es){
    if(es[0].isIntersecting) loadNewer();
  }, {rootMargin:'0px 0px 800px 0px'});
  IO_TOP.observe(topS); IO_BOTTOM.observe(botS);
}
function mkSentinel(id){ var d=document.createElement('div'); d.id=id; d.className='sentinel'; d.style.height='1px'; return d; }
// After any DOM mutation, re-assert the sentinels as the very first
// and last children of .chat (otherwise an inserted .day might land
// outside them and break paging).
function keepSentinelsAtEnds(chatEl){
  var topS=document.getElementById('sentinel-top'), botS=document.getElementById('sentinel-bottom');
  if(topS && chatEl.firstChild!==topS) chatEl.insertBefore(topS, chatEl.firstChild);
  if(botS && chatEl.lastChild!==botS) chatEl.appendChild(botS);
}

function htmlToNodes(htmlStr){
  var tmp=document.createElement('div'); tmp.innerHTML=htmlStr;
  return [].slice.call(tmp.children);
}
function mergeAdjacentSameDay(prevDay, nextDay){
  if(!prevDay||!nextDay) return false;
  if(!prevDay.getAttribute||!nextDay.getAttribute) return false;
  if(prevDay.getAttribute('data-key')!==nextDay.getAttribute('data-key')) return false;
  var aInner=prevDay.querySelector('.dayinner'), bInner=nextDay.querySelector('.dayinner');
  if(aInner&&bInner){ while(bInner.firstChild) aInner.appendChild(bInner.firstChild); }
  nextDay.parentNode.removeChild(nextDay);
  return true;
}

function loadOlder(){
  if(LIVE.suppressOlder || LIVE.loadingOlder || !LIVE.hasMore || !LIVE.messages.length) return;
  LIVE.loadingOlder=true;
  var oldestTs=LIVE.messages[0].ts;
  api('/api/chat/'+slug+'/before/'+oldestTs+'?limit='+PAGE_LIMIT).then(function(page){
    if(!page.messages.length){ LIVE.hasMore=false; LIVE.loadingOlder=false; return; }
    var chatEl=document.querySelector('.chat');
    // Scroll anchor: the first real .day element + its current Y offset.
    var anchor=chatEl.querySelector('.day');
    var anchorTop=anchor?anchor.getBoundingClientRect().top:0;
    LIVE.messages=page.messages.concat(LIVE.messages);
    LIVE.hasMore=page.has_more;
    var nodes=htmlToNodes(buildChatHTML(page.messages, 0, LIVE.meta.is_group).html);
    var topS=document.getElementById('sentinel-top');
    var insBefore=topS?topS.nextSibling:chatEl.firstChild;   // right after the top sentinel
    for(var i=nodes.length-1;i>=0;i--){ chatEl.insertBefore(nodes[i], insBefore); insBefore=nodes[i]; }
    mergeAdjacentSameDay(nodes[nodes.length-1], anchor);
    keepSentinelsAtEnds(chatEl);
    // Keep the scroll position visually stable: pull the anchor back
    // to its original viewport Y. Done AFTER a layout frame, otherwise
    // getBoundingClientRect would still report the old position and
    // the correction would silently land on the wrong row.
    if(anchor){
      requestAnimationFrame(function(){
        var delta = anchor.getBoundingClientRect().top - anchorTop;
        if(delta) window.scrollBy(0, delta);
        if(typeof rebuildDays==='function') rebuildDays();   // recompute the top-visible day marker
      });
    }
    markFreshNodes(nodes);
    refreshDaysIndex();
    LIVE.loadingOlder=false;
  }).catch(function(){ LIVE.loadingOlder=false; });
}

function loadNewer(){
  if(LIVE.suppressNewer || LIVE.loadingNewer || !LIVE.hasNewer || !LIVE.messages.length) return;
  LIVE.loadingNewer=true;
  var newestTs=LIVE.messages[LIVE.messages.length-1].ts;
  api('/api/chat/'+slug+'/since/'+newestTs).then(function(d){
    var add=(d.messages||[]).filter(function(m){return m.ts>newestTs;});
    if(!add.length){ LIVE.hasNewer=false; LIVE.loadingNewer=false; return; }
    var chatEl=document.querySelector('.chat');
    var lastDay=chatEl.querySelector('.day:last-of-type');
    LIVE.messages=LIVE.messages.concat(add);
    var existingDays=chatEl.querySelectorAll('.day').length;
    var nodes=htmlToNodes(buildChatHTML(add, existingDays, LIVE.meta.is_group).html);
    var botS=document.getElementById('sentinel-bottom');
    nodes.forEach(function(nd){ chatEl.insertBefore(nd, botS); });
    if(lastDay && nodes.length) mergeAdjacentSameDay(lastDay, nodes[0]);
    keepSentinelsAtEnds(chatEl);
    // Have we reached the true end? (last loaded ts == chat's last ts)
    var lastLoaded=LIVE.messages[LIVE.messages.length-1].ts;
    if(LIVE.meta && LIVE.meta.stats && lastLoaded>=LIVE.meta.stats.last) LIVE.hasNewer=false;
    markFreshNodes(nodes);
    refreshDaysIndex();
    LIVE.loadingNewer=false;
  }).catch(function(){ LIVE.loadingNewer=false; });
}

// Append messages pushed via the live WebSocket (new iMessages
// arriving on a `mac_live` source). Only runs when the bottom of the
// chat is currently loaded — if there are unloaded "newer" messages
// between us and the new push, we'd otherwise drop the new ones in
// the wrong position.
function appendLive(newMsgs){
  if(!newMsgs || !newMsgs.length) return;
  if(EDIT_MODE.on) return;   // don't append into the edit-mode view
  if(LIVE.hasNewer) return;
  var lastTs=LIVE.messages.length?LIVE.messages[LIVE.messages.length-1].ts:0;
  var add=newMsgs.filter(function(m){return m.ts>lastTs;});
  if(!add.length) return;
  // New messages might bring media — invalidate the cached full-media
  // list so the next overlay open picks them up.
  if(add.some(function(m){return m.media && m.media.length;})) MEDIA_FULL_LOADED=false;
  var atBottom=(window.innerHeight+window.scrollY)>=(document.body.scrollHeight-150);
  var chatEl=document.querySelector('.chat');
  var lastDay=chatEl.querySelector('.day:last-of-type');
  LIVE.messages=LIVE.messages.concat(add);
  var existingDays=chatEl.querySelectorAll('.day').length;
  var nodes=htmlToNodes(buildChatHTML(add, existingDays, LIVE.meta.is_group).html);
  var botS=document.getElementById('sentinel-bottom');
  nodes.forEach(function(nd){ chatEl.insertBefore(nd, botS); });
  if(lastDay && nodes.length) mergeAdjacentSameDay(lastDay, nodes[0]);
  coalesceDays(chatEl);   // merge any adjacent .day blocks with the same key (robust)
  keepSentinelsAtEnds(chatEl);
  markFreshNodes(nodes);
  refreshDaysIndex();
  if(atBottom) window.scrollTo({top:document.body.scrollHeight, behavior:'smooth'});
}

// Merge EVERY pair of adjacent .day elements that share the same
// data-key. Guards against duplicate day headers after live appends
// (especially when several pushes hit in quick succession).
function coalesceDays(chatEl){
  var days=[].slice.call(chatEl.querySelectorAll('.day'));
  for(var i=days.length-1;i>0;i--){
    if(days[i].getAttribute('data-key')===days[i-1].getAttribute('data-key')){
      mergeAdjacentSameDay(days[i-1], days[i]);
    }
  }
}

function markFreshNodes(nodes){
  var rows=[];
  nodes.forEach(function(nd){ if(nd.querySelectorAll) rows=rows.concat([].slice.call(nd.querySelectorAll('.row'))); });
  rows.forEach(function(r){ r.classList.add('row-fresh'); });
  setTimeout(function(){ rows.forEach(function(r){ r.classList.remove('row-fresh'); }); }, 900);
}

function refreshDaysIndex(){
  if(typeof rebuildDays==='function') rebuildDays();
}

// Jump to a calendar day (key = "YYYY-MM-DD"). If it's already in the
// loaded window, just scroll. Otherwise (v2 live mode) fetch a slice
// around the day, rebuild the DOM, then scroll into place.
function jumpToDayKey(key){
  if(!key) return;
  var target=document.querySelector('.day[data-key="'+key+'"]');
  if(target){
    scrollToDayElementStable(target);
    // Explicitly mark the target day as active — required when the
    // day can't be scrolled all the way to the top (e.g. the very
    // last day of the chat); otherwise upd() would pick the wrong row.
    if(typeof setActiveDayKey==='function') setActiveDayKey(key);
    // Pin the marker briefly so the scroll-settle event doesn't
    // overwrite the explicit selection we just made.
    LIVE.pinnedDayKey=key;
    setTimeout(function(){
      LIVE.pinnedDayKey=null;
      if(typeof setActiveDayKey==='function') setActiveDayKey(key);
    }, 700);
    return;
  }
  if(!LIVE.meta) return;   // static mode: everything is already loaded, nothing to fetch
  // Midnight (local) of that day, expressed as a Unix ts
  var parts=key.split('-');
  var ts=Math.floor(new Date(+parts[0], +parts[1]-1, +parts[2], 0,0,0).getTime()/1000);
  // Load enough context that the page is scrollable (otherwise the top
  // sentinel would fire instantly after the rebuild and the marker
  // would jump away). The target lands via an explicit scroll anchor;
  // sentinels stay suspended for the duration of the jump.
  LIVE.suppressOlder=true; LIVE.suppressNewer=true;
  LIVE.pinnedDayKey=key;   // this day stays marked regardless of subsequent loads
  api('/api/chat/'+slug+'/around/'+ts+'?before=80&after=120').then(function(d){
    if(!d.messages.length){ LIVE.suppressOlder=false; LIVE.suppressNewer=false; LIVE.pinnedDayKey=null; return; }
    LIVE.messages=d.messages.slice();
    LIVE.hasMore=true;
    var lastLoaded=LIVE.messages[LIVE.messages.length-1].ts;
    LIVE.hasNewer = !(LIVE.meta.stats && lastLoaded>=LIVE.meta.stats.last);
    rebuildChatDOM();
    requestAnimationFrame(function(){
      var t2=document.querySelector('.day[data-key="'+key+'"]');
      if(t2) scrollToDayElementStable(t2);
      if(typeof setActiveDayKey==='function') setActiveDayKey(key);
      setTimeout(function(){
        LIVE.suppressOlder=false; LIVE.suppressNewer=false;
        if(typeof setActiveDayKey==='function') setActiveDayKey(key);
        LIVE.pinnedDayKey=null;
      }, 1600);
    });
  }).catch(function(){ LIVE.suppressOlder=false; LIVE.suppressNewer=false; LIVE.pinnedDayKey=null; });
}

// Rebuild .chat from scratch from LIVE.messages. Used by the
// jump-load path — after fetching a new window of messages we throw
// away the old DOM and re-render fresh.
function rebuildChatDOM(){
  var chatEl=document.querySelector('.chat');
  if(!chatEl) return;
  MEDIA={image:[],video:[],audio:[],other:[]};
  var built=buildChatHTML(LIVE.messages, 0, LIVE.meta.is_group);
  chatEl.innerHTML=built.html;
  setupSentinels();   // re-attach sentinels + observers to the new window
  refreshDaysIndex();
}

// WebSocket live connection: on an "update" event for our chat slug,
// fetch the new messages since our last loaded ts and appendLive() them.
function connectLive(){
  try{
    var proto=location.protocol==='https:'?'wss':'ws';
    var ws=new WebSocket(proto+'://'+location.host+'/ws');
    ws.onmessage=function(ev){
      var msg; try{msg=JSON.parse(ev.data);}catch(e){return;}
      if(msg.type==='update' && (msg.chats||[]).some(function(c){return c.slug===slug;})){
        var sinceTs=LIVE.messages.length?LIVE.messages[LIVE.messages.length-1].ts:0;
        api('/api/chat/'+slug+'/since/'+sinceTs).then(function(d){ appendLive(d.messages); });
      }
    };
    ws.onclose=function(){ setTimeout(connectLive, 3000); }; // reconnect
    LIVE.ws=ws;
  }catch(e){ /* WS optional */ }
}

var DAYS=[];          // all .day sections, in chronological order
var curDayIdx=0;      // index into DAYS of the currently top-visible day

// scrollOffset() returns the chat header's *measured* height so the
// day header lands flush under it (a hard-coded 64 would drift if the
// header wraps to two lines on a narrow viewport).
function scrollOffset(){
  var h=document.querySelector('.header');
  return (h?h.offsetHeight:64);
}
// Scroll to a specific day index, parking its date row right under
// the chat header. One clean scrollTo, no follow-up — the smooth
// correcting variant lives in scrollToDayElementStable() below.
function scrollToDay(idx){
  if(idx<0||idx>=DAYS.length)return;
  var el=DAYS[idx];
  var head=el.querySelector('.dayhead')||el;
  var want=Math.max(0, head.getBoundingClientRect().top+window.scrollY-scrollOffset());
  // ONE clean scroll. No follow-up corrections here — earlier versions
  // looped and produced visible jitter on lazy-image height changes.
  window.scrollTo({top:want, behavior:'auto'});
}

// Stable jump to a specific .day ELEMENT: scrolls into place and then
// repeatedly nudges the position over ~1.4s, because lazy images
// ABOVE the target may load and shift everything down. Cancels itself
// the moment the user scrolls manually (wheel / touchmove).
function scrollToDayElementStable(el){
  if(!el) return;
  function want(){
    var head=el.querySelector('.dayhead')||el;
    return Math.max(0, head.getBoundingClientRect().top+window.scrollY-scrollOffset());
  }
  window.scrollTo({top:want(), behavior:'auto'});
  var tries=0, cancelled=false, lastSet=window.scrollY;
  function onUser(){ if(Math.abs(window.scrollY-lastSet)>4) cancelled=true; }
  window.addEventListener('wheel', function h(){cancelled=true; window.removeEventListener('wheel',h);}, {passive:true});
  window.addEventListener('touchmove', function h(){cancelled=true; window.removeEventListener('touchmove',h);}, {passive:true});
  function correct(){
    if(cancelled) return;
    var w=want();
    if(Math.abs(window.scrollY-w)>3){ lastSet=w; window.scrollTo({top:w, behavior:'auto'}); }
    tries++;
    if(tries<12) setTimeout(correct, 120);  // ~1.4s of follow-up until lazy images finish loading
  }
  setTimeout(correct, 100);
}
// Arrow-key navigation: jump to previous/next day.
window.jumpDay=function(dir){ scrollToDay(curDayIdx+dir); };
// Collapse / expand the heatmap (toggle in the date panel).
window.toggleHeatmap=function(){
  var p=document.getElementById('daypanel');
  var icon=document.querySelector('#df-toggle i');
  var collapsed=p.classList.toggle('collapsed');
  if(icon) icon.className='fa-solid '+(collapsed?'fa-chevron-down':'fa-chevron-up');
  try{ localStorage.setItem('mv_heatmap_collapsed', collapsed?'1':'0'); }catch(e){}
};

// ---- Custom voice-note player (used inside chat bubbles) ------------------
// We don't use the native <audio> controls because their styling
// can't be matched to the bubble look. Instead each .vplayer hosts a
// hidden <audio> plus the play button + a click-/drag-able progress
// bar that we paint manually from the audio's timeupdate events.
var vpCurrent=null;   // currently playing <audio>, or null
function vpEls(vp){return {au:vp.querySelector('audio'),btn:vp.querySelector('.vp-play i'),
  fill:vp.querySelector('.vp-fill'),knob:vp.querySelector('.vp-knob'),time:vp.querySelector('.vp-time')};}
function vpPaint(vp){
  var e=vpEls(vp), d=e.au.duration||0, t=e.au.currentTime||0, pct=d?(t/d*100):0;
  e.fill.style.width=pct+'%'; e.knob.style.left=pct+'%';
  e.time.textContent=fmtDur(t>0?t:d);   // before play: show total length; during: current time
}
window.vpToggle=function(vp){
  var e=vpEls(vp);
  if(!e.au) return;
  if(e.au.paused){
    if(vpCurrent && vpCurrent!==e.au){vpCurrent.pause();}  // stop the other one
    e.au.play(); vpCurrent=e.au; vp.classList.add('playing');
    e.btn.className='fa-solid fa-pause';
    if(!e.au._wired){
      e.au._wired=true;
      e.au.addEventListener('timeupdate',function(){vpPaint(vp);});
      e.au.addEventListener('loadedmetadata',function(){vpPaint(vp);});
      e.au.addEventListener('ended',function(){vp.classList.remove('playing');e.btn.className='fa-solid fa-play';vpPaint(vp);});
      e.au.addEventListener('pause',function(){vp.classList.remove('playing');e.btn.className='fa-solid fa-play';});
    }
  }else{
    e.au.pause();
  }
};
window.vpSeek=function(vp,clientX){
  var e=vpEls(vp), bar=vp.querySelector('.vp-bar'), r=bar.getBoundingClientRect();
  var ratio=Math.min(1,Math.max(0,(clientX-r.left)/r.width));
  function doSeek(){ if(e.au.duration){e.au.currentTime=ratio*e.au.duration; vpPaint(vp);} }
  if(e.au.readyState>=1) doSeek();
  else { e.au.addEventListener('loadedmetadata',doSeek,{once:true}); e.au.load(); }
};

// Tag a media bubble as "portrait" based on the browser's ACTUAL
// rendered dimensions (which honour EXIF rotation — `sips` at export
// time does not, so we can't rely on the file metadata alone). Only
// applies to single-image bubbles; gallery tiles have their own layout.
function markPortrait(img){
  if(!img || img.naturalWidth<=0) return;
  var bubble=img.closest('.bubble.media');
  if(!bubble || img.closest('.gallery')) return;            // galleries use their own grid layout
  if(img.naturalHeight > img.naturalWidth) bubble.classList.add('portrait');
  else bubble.classList.remove('portrait');
}
// Wire up the global click/keyboard handlers that drive everything
// outside the SPA's rerenders: edit-pill toggles, lightbox open/close,
// heatmap cell → day jump, voice-player play/pause + seek, and the
// keyboard shortcuts (Esc / arrows) inside the lightbox.
function initInteractions(){
  // lazysizes fires 'lazyloaded' once the real <img> src has loaded.
  document.addEventListener('lazyloaded',function(e){ markPortrait(e.target); });
  // Catch already-loaded / cached images that won't fire lazyloaded.
  document.querySelectorAll('.bubble.media > img').forEach(function(img){
    if(img.complete && img.naturalWidth>0) markPortrait(img);
    else img.addEventListener('load',function(){markPortrait(img);},{once:true});
  });
  document.addEventListener('click',function(e){
    // "Edited" pill on a message: toggle the diff block.
    var et=e.target.closest('.edit-toggle');
    if(et){
      var det=et.parentNode.querySelector('.edit-detail');
      if(det){ det.hidden=!det.hidden; et.classList.toggle('open',!det.hidden); }
      return;
    }
    // Lightbox is open: clicking the backdrop closes it.
    var lb=document.getElementById('lightbox');
    if(lb.classList.contains('open') && e.target.closest('#lightbox')){
      if(e.target.closest('.lb-nav')) {/* let the nav buttons handle their own clicks */}
      else if(lb.classList.contains('lb-isfile')){
        // File (PDF/vCard): only a backdrop click closes — the content stays interactive.
        if(e.target.id==='lightbox'){ lbClose(); return; }
      } else if(lb.classList.contains('single')){
        // Single image: a click on the image OR the backdrop closes.
        lbClose(); return;
      }
    }
    var single=e.target.closest('[data-single]');
    if(single){var it=JSON.parse(single.getAttribute('data-single'));lbOpen([it],0);return;}
    var gi=e.target.closest('.gi');
    if(gi){var gal=gi.closest('.gallery');var list=JSON.parse(gal.getAttribute('data-imgs'));
      var idx=[].indexOf.call(gal.querySelectorAll('.gi'),gi);lbOpen(list,idx<0?0:idx);return;}
    // Heatmap cell → jump to that day (loading the window if needed).
    var hc=e.target.closest('.hm-cell.has');
    if(hc){ jumpToDayKey(hc.getAttribute('data-day')); return; }
    // Voice player: Play/Pause button.
    var pb=e.target.closest('.vp-play');
    if(pb){ vpToggle(pb.closest('.vplayer')); return; }
    // Voice player: click on the progress bar = seek.
    var bar=e.target.closest('.vp-bar');
    if(bar){ vpSeek(bar.closest('.vplayer'), e.clientX); return; }
  });
  document.addEventListener('keydown',function(e){
    if(!document.getElementById('lightbox').classList.contains('open'))return;
    if(e.key==='Escape')lbClose();
    if(LB.length<=1)return;                 // single image: no prev/next
    if(e.key==='ArrowLeft')lbNav(-1); if(e.key==='ArrowRight')lbNav(1);
  });
  // Media overview: Escape closes it (the chip toggle stays the primary path).
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape' && MO_TAB && document.getElementById('mediaoverlay').classList.contains('open')) moClose();
  });

  var panel=document.getElementById('daypanel');
  var label=document.getElementById('dateflag-label');
  var prevBtn=document.getElementById('df-prev'),nextBtn=document.getElementById('df-next');
  var totop=document.getElementById('totop');
  var headerEl=document.querySelector('.header');
  DAYS=[].slice.call(document.querySelectorAll('.day'));
  var current=-1;

  // gespeicherten Heatmap-Klappzustand wiederherstellen
  try{
    if(localStorage.getItem('mv_heatmap_collapsed')==='1'){
      panel.classList.add('collapsed');
      var ic=document.querySelector('#df-toggle i');
      if(ic) ic.className='fa-solid fa-chevron-down';
    }
  }catch(e){}

  // Park the entire side panel (date nav + heatmap) flush under the chat header.
  function placeFlag(){
    if(headerEl&&panel){panel.style.top=(headerEl.offsetHeight+12)+"px";}
  }
  placeFlag();

  var hmCellByKey={};   // "YYYY-MM-DD" → heatmap cell (used to toggle the .current marker)
  [].forEach.call(document.querySelectorAll('.hm-cell[data-day]'),function(c){
    hmCellByKey[c.getAttribute('data-day')]=c;
  });
  var activeCell=null;

  // THE single function that updates "current day": panel label + heatmap marker.
  function setActiveDay(idx){
    var d=DAYS[idx]; if(!d)return;
    label.innerHTML='<span class="wd">'+(d.getAttribute('data-wd')||'')+'</span>'+d.getAttribute('data-date');
    prevBtn.disabled=(idx<=0);
    nextBtn.disabled=(idx>=DAYS.length-1);
    // Heatmap marker.
    if(activeCell)activeCell.classList.remove('current');
    var dk=d.getAttribute('data-key');
    var cell=hmCellByKey[dk];
    if(cell){
      cell.classList.add('current'); activeCell=cell;
      // Scroll the heatmap so the marked cell stays visible inside its own container.
      scrollHeatmapToCell(cell);
    } else { activeCell=null; }
    // (Persistence is handled separately, debounced, only on real user scroll.)
  }
  function upd(){
    // During a heatmap-jump, the clicked day stays pinned as the active marker.
    if(LIVE && LIVE.pinnedDayKey){ panel.classList.add('show'); return; }
    if(DAYS.length){
      var best=0,bt=-1e9,th=scrollOffset()+6;
      for(var i=0;i<DAYS.length;i++){var r=DAYS[i].getBoundingClientRect();if(r.top<=th&&r.top>bt){bt=r.top;best=i;}}
      curDayIdx=best;
      if(best!==current){current=best;setActiveDay(best);}
      panel.classList.add('show');   // panel is always visible (no auto-hide)
    }
    if(window.scrollY>500)totop.classList.add('show');else totop.classList.remove('show');
  }
  var tick=false;
  window.addEventListener('scroll',function(){if(!tick){requestAnimationFrame(function(){upd();tick=false;});tick=true;}},{passive:true});
  window.addEventListener('resize',function(){placeFlag();upd();moPosition();},{passive:true});upd();

  // After a DOM change (paging or rebuild): rebuild DAYS + heatmap cell index.
  rebuildDays=function(){
    DAYS=[].slice.call(document.querySelectorAll('.day'));
    hmCellByKey={};
    [].forEach.call(document.querySelectorAll('.hm-cell[data-day]'),function(c){
      hmCellByKey[c.getAttribute('data-day')]=c;
    });
    current=-1; upd();
  };
  // Force a specific day (by data-key) to be marked as the current
  // one. Used by jumps where upd()'s "topmost in viewport" heuristic
  // would pick a different row than the one the user actually asked for.
  setActiveDayKey=function(key){
    DAYS=[].slice.call(document.querySelectorAll('.day'));
    for(var i=0;i<DAYS.length;i++){
      if(DAYS[i].getAttribute('data-key')===key){ current=i; setActiveDay(i); return; }
    }
  };
}
var rebuildDays=null;
var setActiveDayKey=null;

var slug=pathSlug();
if(!slug){document.body.innerHTML='<div style="padding:40px;color:#ccc">No chat specified. <a href="/" style="color:#3a9bff">Back to index</a></div>';return;}

// --- Pick the load mode: v2 live server (paginated API) vs. static JSON ----
// The v2 server exposes /api/index. If it's not there (file:// open or
// an older deployment), we fall back to the static full-JSON snapshot —
// the original pre-v2 path. INITIAL_LIMIT/PAGE_LIMIT only apply in v2.
var INITIAL_LIMIT=60, PAGE_LIMIT=60;

function loadStatic(){
  Promise.all([
    fetch(mvUrl('/data/chats/')+slug+'.json').then(function(r){if(!r.ok)throw new Error('nf');return r.json();}),
    fetch(mvUrl('/data/transcripts.json')).then(function(r){return r.ok?r.json():{};}).catch(function(){return {};}),
    fetch(mvUrl('/data/ocr.json')).then(function(r){return r.ok?r.json():{};}).catch(function(){return {};})
  ]).then(function(res){
    TRANSCRIPTS=res[1]||{};
    OCR=res[2]||{};
    window.__staticMessages=(res[0].messages||[]);   // used by the edit overlay in static mode
    render(res[0]);
  }).catch(function(){
    document.body.innerHTML='<div style="padding:40px;color:#ccc">Chat "'+esc(slug)+'" not found. <a href="/" style="color:#3a9bff">Back to index</a></div>';
  });
}

function api(path){ var u=(window.mvUrl?window.mvUrl(path):path); return fetch(u).then(function(r){if(!r.ok)throw new Error('http '+r.status);return r.json();}); }

function loadLive(){
  // Fan out the initial fetches in parallel: meta + the newest page
  // of messages + transcripts/OCR caches + the full per-day stats for
  // the heatmap. They're independent, so the network round-trip cost
  // is gated by the slowest of the six, not the sum.
  Promise.all([
    api('/api/chat/'+slug+'/meta'),
    api('/api/chat/'+slug+'/latest?limit='+INITIAL_LIMIT),
    fetch(mvUrl('/data/transcripts.json')).then(function(r){return r.ok?r.json():{};}).catch(function(){return {};}),
    api('/api/chat/'+slug+'/edited').catch(function(){return {total:0};}),
    api('/api/chat/'+slug+'/days').catch(function(){return {days:null};}),
    fetch(mvUrl('/data/ocr.json')).then(function(r){return r.ok?r.json():{};}).catch(function(){return {};})
  ]).then(function(res){
    var meta=res[0], page=res[1];
    TRANSCRIPTS=res[2]||{};
    OCR=res[5]||{};
    LIVE.meta=meta;
    LIVE.messages=page.messages.slice();
    LIVE.hasMore=page.has_more;
    LIVE.editedTotal=res[3].total||0;   // true total of edited messages (drives the "Edited" pill count)
    LIVE.days=res[4].days||null;        // complete per-day stats for the heatmap
    // Assemble the chat object in the format render() expects.
    render(liveChatObj());
    initLivePaging();
    // Entry point: restore the saved scroll position if we have one,
    // otherwise jump to the bottom (newest message).
    var saved=loadSavedDay(slug);
    if(saved && document.querySelector('.hm-cell.has[data-day="'+saved+'"]')){
      // Tiny delay so the initial render has fully settled before the
      // jump triggers a window-load (avoids a race with the initial scroll).
      setTimeout(function(){ jumpToDayKey(saved); }, 80);
    } else {
      requestAnimationFrame(function(){
        // Newest is at the bottom: scroll to page end, then keep
        // nudging because lazy-load images change the page height as
        // they finish loading (otherwise we'd stop short).
        var n=0;
        (function toBottom(){
          window.scrollTo({top:document.body.scrollHeight, behavior:'auto'});
          if(++n<15) setTimeout(toBottom, 120);
        })();
        var days=document.querySelectorAll('.day');
        if(days.length && typeof setActiveDayKey==='function'){
          setActiveDayKey(days[days.length-1].getAttribute('data-key'));
        }
      });
    }
  }).catch(function(e){
    // v2 unavailable → fall back to the static-JSON path.
    loadStatic();
  });
}

// Scroll the heatmap so the currently-active cell sits roughly in the
// middle of its container. At the boundaries (very first or very last
// month of the chat) we clamp instead of centring — otherwise the
// heatmap would show a big empty strip.
//
// Important detail: this function gets called up to 60×/s during a
// page scroll. Using scrollTo({behavior:'smooth'}) here would cancel
// every previous animation halfway through and produce a "sticky"
// heatmap. We deliberately set scrollTop directly (jump-cut) instead,
// which produces a perfect 1:1 sync with the page scroll.
function scrollHeatmapToCell(cell){
  var sc=document.querySelector('.hm-scroll');
  if(!sc||!cell) return;
  var scRect=sc.getBoundingClientRect(), cRect=cell.getBoundingClientRect();
  // Target: cell vertically centred. delta = how much further we'd need to scroll.
  var delta=(cRect.top - scRect.top) - (sc.clientHeight/2) + (cRect.height/2);
  var target=sc.scrollTop + delta;
  // Clamp at the boundaries so we never expose a blank strip above/below.
  var maxScroll=sc.scrollHeight - sc.clientHeight;
  if(target<0) target=0;
  if(target>maxScroll) target=maxScroll;
  if(Math.abs(target - sc.scrollTop) < 1) return;
  sc.scrollTop=target;
}

// --- Persist scroll position per chat (localStorage) -----------------------
function savedDayKey(chatSlug){ return 'mv_pos_'+chatSlug; }
function saveCurrentDay(chatSlug, dayKey){
  if(!dayKey) return;
  try{ localStorage.setItem(savedDayKey(chatSlug), dayKey); }catch(e){}
}
function loadSavedDay(chatSlug){
  try{ return localStorage.getItem(savedDayKey(chatSlug)); }catch(e){ return null; }
}

// Build a chat object in the shape render() expects, derived from
// LIVE.meta + LIVE.messages. Both static mode and live mode go
// through the same render() — this adapter is what makes that work.
function liveChatObj(){
  var m=LIVE.meta;
  return {
    slug:m.slug, title:m.title, subtitle:m.subtitle, is_group:m.is_group,
    me_name:m.me_name, origin:m.origin, device:m.device,
    owner_avatar:m.owner_avatar, chat_avatar:m.chat_avatar,
    stats:{ total:m.stats.total, me:m.stats.me, them:m.stats.them,
            first:m.stats.first, last:m.stats.last,
            media:m.stats.media||{image:{me:0,them:0},video:{me:0,them:0},audio:{me:0,them:0},other:{me:0,them:0}},
            bytes:m.stats.bytes||{}, bytes_total:m.stats.bytes_total||0, bytes_orig:m.stats.bytes_orig||0 },
    messages:LIVE.messages
  };
}

// v2 live-mode state: everything the paginated path needs to track.
// `suppressOlder`/`suppressNewer` block sentinels during programmatic
// jumps; `pinnedDayKey` overrides the topmost-visible heuristic;
// `userEngaged` gates the localStorage save so initial autoscrolls
// don't overwrite the previously-saved position.
var LIVE={meta:null, messages:[], hasMore:false, hasNewer:false, loadingOlder:false, loadingNewer:false, suppressOlder:false, suppressNewer:false, pinnedDayKey:null, userEngaged:false, preFilterDayKey:null};

// Pick v2 (paginated API) vs. static (single JSON) by probing for /api/index.
fetch(mvUrl('/api/index'),{method:'GET'}).then(function(r){
  if(r.ok) loadLive(); else loadStatic();
}).catch(function(){ loadStatic(); });
})();
