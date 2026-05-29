/* Renders the index page from data/index.json (grouped by device). */
(function(){
"use strict";
var MOS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function esc(s){return (s||"").replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function nf(n){return (n||0).toLocaleString("en-US");}
function D(ts){return new Date(ts*1000);}
function dayShort(d){return MOS[d.getMonth()]+" "+d.getDate()+", "+d.getFullYear();}
function initials(t){
  // Strip parenthesized suffixes (e.g. "(iCloud)") then take the first letter of each real word.
  var base=(t||"").replace(/\([^)]*\)/g," ");
  var words=base.split(/\s+/).map(function(w){var m=/[\p{L}\p{N}]/u.exec(w);return m?w.slice(m.index):"";}).filter(Boolean);
  return words.slice(0,2).map(function(w){return w[0];}).join("").toUpperCase();
}
function fmtBytes(b){
  b=b||0;
  if(b>=1073741824)return (b/1073741824).toFixed(1).replace(".",",")+" GB";
  if(b>=1048576)return Math.round(b/1048576)+" MB";
  if(b>=1024)return Math.round(b/1024)+" KB";
  return b+" B";
}
// Messaging service -> logo (FA) + class + plain text
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
function originBadge(o){
  var i=originInfo(o);
  return '<span class="origin-badge origin-'+(o||'apple')+'" title="'+i.name+'"><i class="'+i.icon+'"></i></span>';
}

function firstName(n){return (n||"").trim().split(/\s+/)[0]||"";}
// Render an avatar: image when src is set, initials otherwise.
// Image falls back to initials if loading fails.
function avatarHtml(src, title){
  var ini=esc(initials(title));
  if(src){
    var url=mvUrl('/'+src);
    return '<div class="av av-img-wrap">'+
      '<img class="av-img" src="'+esc(url)+'" alt="'+ini+'" '+
        'onerror="this.style.display=\'none\';this.nextSibling.style.display=\'flex\';"/>'+
      '<span class="av-fallback" style="display:none;">'+ini+'</span>'+
    '</div>';
  }
  return '<div class="av">'+ini+'</div>';
}
function card(c){
  // Show owner and the other party with first names only (for consistency).
  var me=firstName(c.me_name)||"Me";
  var period="";
  if(c.first&&c.last)period=dayShort(D(c.first))+" – "+dayShort(D(c.last));
  function mc(typ,icon,label){
    var n=c.media[typ].me+c.media[typ].them; if(!n)return"";
    return '<span><i class="'+icon+'"></i> '+n+' '+label+'</span>';
  }
  var sizeBadge="";
  if(c.bytes_total){
    var orig=c.bytes_orig?(' <span class="orig">+ '+fmtBytes(c.bytes_orig)+' orig.</span>'):'';
    sizeBadge='<span class="sizebadge" title="Web media (what the app loads) + original images"><i class="fa-solid fa-database"></i> '+fmtBytes(c.bytes_total)+orig+'</span>';
  }
  // Live feed vs. static import snapshot.
  var feedBadge;
  if(c.live){
    feedBadge='<span class="feedbadge live" title="Live feed — updates automatically"><span class="livedot"></span> Live</span>';
  } else {
    var stand=c.last?dayShort(D(c.last)):'';
    feedBadge='<span class="feedbadge static" title="Static import — fixed snapshot"><i class="fa-solid fa-box-archive"></i> Import · until '+esc(stand)+'</span>';
  }
  return ''+
  '<a class="card" data-slug="'+esc(c.slug)+'"'+(c.live?' data-live="1"':'')+' data-total="'+(c.total||0)+'" href="/chat/'+c.slug.split('/').map(encodeURIComponent).join('/')+'">'+
    '<span class="newbadge" hidden></span>'+
    '<div class="top"><div class="avwrap">'+avatarHtml(c.chat_avatar, c.title)+originBadge(c.origin)+'</div>'+
      '<div><div class="nm">'+esc(c.title)+(c.is_group?'<span class="badge-group"><i class="fa-solid fa-user-group"></i> GROUP</span>':'')+'</div>'+
      '<div class="pd"><i class="fa-regular fa-calendar"></i> '+esc(period)+'</div></div></div>'+
    '<div class="feedline">'+feedBadge+'</div>'+
    '<div class="big">'+nf(c.total)+'<small>messages</small></div>'+
    '<div class="split"><span class="s">'+esc(me)+': '+nf(c.me)+'</span> · '+(c.is_group?'Others':esc(c.title.split(" ")[0]))+': '+nf(c.them)+'</div>'+
    (sizeBadge?'<div class="sizeline">'+sizeBadge+'</div>':'')+
    '<div class="mline">'+mc('image','fa-solid fa-image','Images')+mc('video','fa-solid fa-film','Videos')+mc('audio','fa-solid fa-microphone','Voice')+'</div>'+
  '</a>';
}

// v2-Live-API bevorzugen, sonst statische index.json (file:// / alter Server)
function loadIndex(){
  return fetch(mvUrl('/api/index')).then(function(r){
    if(!r.ok) throw new Error('no-api');
    return r.json();
  }).catch(function(){
    return fetch(mvUrl('/data/index.json')).then(function(r){return r.json();});
  });
}

loadIndex().then(function(ix){
  var chats=ix.chats||[];
  var devices=ix.devices||[];
  var totalMsgs=chats.reduce(function(a,c){return a+c.total;},0);
  var totalImgs=chats.reduce(function(a,c){return a+c.media.image.me+c.media.image.them;},0);
  var totalBytes=chats.reduce(function(a,c){return a+(c.bytes_total||0);},0);
  var totalOrig=chats.reduce(function(a,c){return a+(c.bytes_orig||0);},0);

  // Group chats by device (order matches the devices array).
  var sections="";
  var multiDevice=devices.length>1;
  function deviceIcon(name){
    var n=(name||"").toLowerCase();
    if(n.indexOf("iphone")>=0)return "fa-mobile-screen-button";
    if(n.indexOf("ipad")>=0)return "fa-tablet-screen-button";
    if(n.indexOf("mac")>=0)return "fa-laptop";
    return "fa-mobile-screen";
  }
  var order=devices.length?devices:[{name:null}];
  order.forEach(function(dev){
    var devChats=chats.filter(function(c){return (c.device||null)===(dev.name||null);});
    if(!devChats.length)return;
    if(multiDevice){
      var dmsgs=devChats.reduce(function(a,c){return a+c.total;},0);
      var dbytes=devChats.reduce(function(a,c){return a+(c.bytes_total||0);},0);
      // Klappzustand aus localStorage holen; per Default offen.
      var devSlug=(dev.slug||dev.name||"").replace(/"/g,'');
      var collapsed=false;
      try{ collapsed=localStorage.getItem('mv_dev_collapsed_'+devSlug)==='1'; }catch(e){}
      sections+='<details class="device-section" data-dev="'+esc(devSlug)+'"'+
        (collapsed?'':' open')+'>'+
        '<summary class="device-head">'+
          '<i class="fa-solid fa-chevron-down device-chev"></i>'+
          '<i class="fa-solid '+deviceIcon(dev.name)+'"></i> '+esc(dev.name||"Device")+
          '<span class="device-meta">'+devChats.length+' chats · '+nf(dmsgs)+' messages · '+fmtBytes(dbytes)+(dev.me_name?(' · '+esc(dev.me_name)):'')+'</span>'+
        '</summary>'+
        '<div class="cards-wrap">'+
          '<div class="cards">'+devChats.map(function(c,i){
            return card(c).replace('<a class="card"','<a class="card" style="--i:'+i+'"');
          }).join("")+
          '</div>'+
        '</div>'+
        '</details>';
    } else {
      sections+='<div class="cards">'+devChats.map(function(c,i){
        return card(c).replace('<a class="card"','<a class="card" style="--i:'+i+'"');
      }).join("")+'</div>';
    }
  });

  var headTitle=multiDevice?"Message Visualizer":("Message Visualizer · "+esc((devices[0]&&devices[0].name)||"Device"));
  var headMeta=chats.length+' chats · '+nf(totalMsgs)+' messages · '+nf(totalImgs)+' images · '+
               fmtBytes(totalBytes)+' web'+(totalOrig?(' + '+fmtBytes(totalOrig)+' orig.'):'')+
               (multiDevice?(' · '+devices.length+' devices'):'');

  document.body.innerHTML=
    '<div class="index-wrap">'+
      '<div class="index-head"><h1><i class="fa-solid fa-comments"></i> '+headTitle+'</h1>'+
        '<div class="meta">'+headMeta+'</div></div>'+
      sections+
    '</div>';

  // Klappzustand der Device-Sections animiert toggeln (max-height-Trick)
  // + Persistenz in localStorage.
  document.querySelectorAll('details.device-section').forEach(function(d){
    var wrap=d.querySelector('.cards-wrap');
    var sum=d.querySelector('summary');
    if(!wrap||!sum) return;

    // Initial state: open → set max-height to scrollHeight so the first
    // close toggle has an actual height to animate from.
    if(d.open){ wrap.style.maxHeight = wrap.scrollHeight + 'px'; }

    var chev=d.querySelector('.device-chev');
    sum.addEventListener('click', function(ev){
      ev.preventDefault();
      var key='mv_dev_collapsed_'+d.getAttribute('data-dev');
      if(d.open){
        // CLOSE
        // 1) Start the cards' fade-out in parallel with the container:
        //    staggered, but in reverse (last card first) so closing feels
        //    natural.
        var cardsC=d.querySelectorAll('.cards .card');
        var totalC=cardsC.length;
        cardsC.forEach(function(card, idx){
          if(card.getAnimations){
            card.getAnimations().forEach(function(a){ a.cancel(); });
          }
          var delay=(totalC - 1 - idx) * 30;
          var anim=card.animate(
            [
              { opacity: 1, filter: 'blur(0px)' },
              { opacity: 0, filter: 'blur(6px)' }
            ],
            {
              duration: 250,
              delay: delay,
              easing: 'ease-in',
              fill: 'forwards'
            }
          );
          anim.onfinish=function(){
            // Keep inline values active until the container is fully
            // collapsed, otherwise the cards snap back for one frame.
            card.style.opacity='0';
            card.style.filter='blur(6px)';
          };
        });
        // 2) Animate the container's height.
        wrap.style.maxHeight = wrap.scrollHeight + 'px';
        void wrap.offsetHeight;          // reflow
        wrap.style.maxHeight = '0px';
        // Rotate the chevron in sync (open is still true, so the CSS
        // default is 0deg → we set inline to -90deg for transition sync).
        if(chev) chev.style.setProperty('--chev-rot','-90deg');
        var onClosed=function(){
          wrap.removeEventListener('transitionend', onClosed);
          d.open=false;
          if(chev) chev.style.removeProperty('--chev-rot');
          // Clean card inline styles so they can come back via the
          // stagger animation on the next open.
          cardsC.forEach(function(card){
            card.style.opacity='';
            card.style.filter='';
          });
        };
        wrap.addEventListener('transitionend', onClosed);
        try{ localStorage.setItem(key,'1'); }catch(e){}
      } else {
        // OPEN
        // 1) Set the cards to "empty" BEFORE setting open=true, so they
        //    don't pop in fully visible for one frame.
        var cards=d.querySelectorAll('.cards .card');
        cards.forEach(function(card){
          if(card.getAnimations){
            card.getAnimations().forEach(function(a){ a.cancel(); });
          }
          card.style.opacity='0';
          card.style.filter='blur(6px)';
        });
        // 2) Set open=true and animate the height.
        d.open=true;
        wrap.style.maxHeight = '0px';
        void wrap.offsetHeight;          // reflow
        wrap.style.maxHeight = wrap.scrollHeight + 'px';
        // 3) Chevron in sync
        if(chev){
          chev.style.setProperty('--chev-rot','-90deg');
          void chev.offsetHeight;
          chev.style.setProperty('--chev-rot','0deg');
        }
        // 4) Start the cards stagger (WAAPI). They are currently invisible
        //    because of the "empty" values — the animation fades them in.
        cards.forEach(function(card, idx){
          var anim=card.animate(
            [
              { opacity: 0, filter: 'blur(6px)' },
              { opacity: 1, filter: 'blur(0px)' }
            ],
            {
              duration: 400,
              delay: idx * 50 + 80,
              easing: 'ease-out',
              fill: 'forwards'
            }
          );
          anim.onfinish=function(){
            card.style.opacity='';
            card.style.filter='';
          };
        });
        var onOpened=function(){
          wrap.removeEventListener('transitionend', onOpened);
          wrap.style.maxHeight = 'none';
          if(chev) chev.style.removeProperty('--chev-rot');
        };
        wrap.addEventListener('transitionend', onOpened);
        try{ localStorage.removeItem(key); }catch(e){}
      }
    });
  });

  // "Unread since last visit": the backend returns new_count = messages
  // with sync_state='new' (real live arrivals from sync). That field is
  // robust against historical bulk imports (old Mac backup, WhatsApp
  // export) that would otherwise inflate the total diff. We still use
  // the total diff as a fallback (for older server versions / migration).
  chats.forEach(function(c){
    if(typeof c.new_count==='number'){
      // primary path: only count real live arrivals.
      markUnread(c.slug, c.new_count);
      // Carry the seen state so a later reload (e.g. after a manual
      // sync_state reset) doesn't suddenly show old counts as "new".
      var seen=seenTotal(c.slug);
      if(seen==null) setSeenTotal(c.slug, c.total||0);
    } else {
      // Fallback: legacy total-diff logic.
      var seenFb=seenTotal(c.slug);
      if(seenFb!=null){ markUnread(c.slug, (c.total||0)-seenFb); }
      else { setSeenTotal(c.slug, c.total||0); }
    }
  });

  // Click on a card = open chat -> mark as read (badge gone).
  // Locally: setSeen + hide badge immediately (otherwise it flickers
  // until the next reload).
  // Server: /api/chat/<slug>/seen marks sync_state='new' entries of
  // this chat as 'published', so new_count is 0 on the next reload.
  document.querySelectorAll('.card[data-slug]').forEach(function(card){
    card.addEventListener('click', function(){
      var slug = card.getAttribute('data-slug');
      setSeenTotal(slug, parseInt(card.getAttribute('data-total')||'0',10));
      markUnread(slug, 0);
      // Fire-and-forget — we don't care about the response.
      try{
        fetch(mvUrl('/api/chat/')+slug.split('/').map(encodeURIComponent).join('/')+'/seen',
              {method:'POST', keepalive:true});
      }catch(e){}
    });
  });

  connectIndexLive();
}).catch(function(e){
  document.body.innerHTML='<div style="padding:40px;color:#ccc">Could not load data/index.json. Please open through a local server.</div>';
});

// --- Unread state per chat (localStorage) ---
function seenTotal(slug){
  try{ var v=localStorage.getItem('mv_seen_'+slug); return v==null?null:parseInt(v,10); }
  catch(e){ return null; }
}
function setSeenTotal(slug,total){
  try{ localStorage.setItem('mv_seen_'+slug, String(total||0)); }catch(e){}
}
// Set/update the NEW badge on a card to nNew (<=0 hides it).
function markUnread(slug, nNew){
  var card=document.querySelector('.card[data-slug="'+(slug||'').replace(/"/g,'')+'"]');
  if(!card) return;
  var badge=card.querySelector('.newbadge');
  if(!badge) return;
  if(nNew>0){ badge.textContent=String(nNew); badge.hidden=false; }
  else{ badge.textContent=''; badge.hidden=true; }
}

// DB connection lost -> show offline badge top-right on every live card.
// online=true removes the badges. Affects only live-feed cards (data-live).
function setDbStatus(online){
  var liveCards=document.querySelectorAll('.card[data-live]');
  for(var i=0;i<liveCards.length;i++){
    var card=liveCards[i];
    var off=card.querySelector('.offlinebadge');
    if(online){
      if(off) off.remove();
    } else if(!off){
      off=document.createElement('span');
      off.className='offlinebadge';
      off.title='Lost connection to the messages DB';
      off.innerHTML='<i class="fa-solid fa-plug-circle-xmark"></i> Offline';
      card.appendChild(off);
    }
  }
}

// WebSocket connection: on "update" recompute the unread counter against
// the stored "seen" state (survives reloads, disappears on chat open).
function connectIndexLive(){
  try{
    var proto=location.protocol==='https:'?'wss':'ws';
    var ws=new WebSocket(proto+'://'+location.host+'/ws');
    ws.onmessage=function(ev){
      var msg; try{msg=JSON.parse(ev.data);}catch(e){return;}
      // DB connection status: on loss, show an offline hint on live cards.
      if(msg.type==='dbstatus'){ setDbStatus(!!msg.online); return; }
      if(msg.type!=='update' || !msg.chats) return;
      msg.chats.forEach(function(ch){
        var card=document.querySelector('.card[data-slug="'+(ch.slug||'').replace(/"/g,'')+'"]');
        if(!card) return;
        if(typeof ch.total==='number'){
          // Carry the current state on the card (for later "mark as read").
          card.setAttribute('data-total', String(ch.total));
          var seen=seenTotal(ch.slug);
          if(seen==null){ seen=ch.total; setSeenTotal(ch.slug, ch.total); }
          markUnread(ch.slug, ch.total-seen);
          // Update the big count if present.
          var big=card.querySelector('.big');
          if(big){ big.firstChild.nodeValue=nf(ch.total); }
        } else {
          // No total provided -> at least add +ch.new.
          var cur=card.querySelector('.newbadge');
          var prev=cur&&!cur.hidden?parseInt(cur.textContent||'0',10):0;
          markUnread(ch.slug, prev+(ch.new||1));
        }
      });
    };
    ws.onclose=function(){ setTimeout(function(){connectIndexLive();}, 3000); };
  }catch(e){ /* WS optional */ }
}
})();
