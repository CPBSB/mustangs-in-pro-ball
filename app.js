const DATA={players:[],stats:{},statsMeta:null,summary:null,daily:null,schedule:null,transactions:null,career:null,archive:null};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const headshot=(id,minor=false)=>`https://img.mlbstatic.com/mlb-photos/image/upload/c_fill,g_auto,w_600,q_auto:best,f_auto/v1/people/${id}/headshot/${minor?'milb/':'67/'}current`;
const logo=id=>id?`https://www.mlbstatic.com/team-logos/${id}.svg`:'';
const levels=['MLB','Triple-A','Double-A','High-A','Single-A','Rookie','Awaiting Pro Assignment','Free Agent'];
function normalizedLevel(p){if(p.status==='fa')return'Free Agent';if(p.status==='mlb')return'MLB';let s=`${p.recentLevel||''} ${p.statusLabel||''}`.toLowerCase();if(s.includes('triple'))return'Triple-A';if(s.includes('double'))return'Double-A';if(s.includes('high'))return'High-A';if(s.includes('single')||s.includes('low-a'))return'Single-A';if(/rookie|acl|fcl|dsl/.test(s))return'Rookie';return'Awaiting Pro Assignment'}
function merge(){DATA.players=DATA.players.map(p=>{let f=DATA.stats[String(p.mlbId)]||{};return{...p,...f,stats:{...(p.stats||{}),...(f.stats||{})}}})}
async function loadAll(){let names=['players','stats','nightly_summary','daily_article','today_schedule','transactions','career_mlb','archive'];let vals=await Promise.all(names.map(n=>fetch(`${n}.json?v=${Date.now()}`,{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null)));DATA.players=vals[0]?.players||[];DATA.statsMeta=vals[1]||null;DATA.stats=vals[1]?.players||{};DATA.summary=vals[2];DATA.daily=vals[3];DATA.schedule=vals[4];DATA.transactions=vals[5];DATA.career=vals[6]||{players:[]};DATA.archive=vals[7]||{editions:[]};merge()}
function portrait(p,cls=''){return`<div class="portrait ${cls}"><img src="${headshot(p.mlbId)}" data-alt="${headshot(p.mlbId,true)}" alt="${esc(p.name)} headshot" onerror="if(!this.dataset.tried){this.dataset.tried='1';this.src=this.dataset.alt}else{this.style.display='none';this.nextElementSibling.style.display='grid'}"><span class="portrait-fallback">${esc(p.initials||'CP')}</span></div>`}
function orders(p){return p.type==='hitter'?['AB','R','H','2B','3B','HR','RBI','BB','SO','SB','CS','AVG','OBP','SLG','OPS']:['IP','R','ER','BB','SO','H','ERA','WHIP']}
function playerCard(p){return`<article class="player-card"><div class="player-top">${portrait(p)}<div><div class="player-name">${esc(p.name)}</div><div class="assignment"><b>${esc(p.position)}</b><br>${esc(p.recentTeam||p.team)} · ${esc(normalizedLevel(p))}</div><span class="level-badge">${esc(p.statusLabel||normalizedLevel(p))}</span></div>${p.teamId?`<img class="team-logo" src="${logo(p.teamId)}" alt="">`:''}</div><div class="stats ${p.type==='pitcher'?'pitcher':''}">${orders(p).map(k=>`<div class="stat"><b>${esc(p.stats?.[k]??'—')}</b><small>${k}</small></div>`).join('')}</div><div class="player-note">${esc(p.note||'')}</div><div class="card-actions"><a href="player.html?id=${p.mlbId}">View Mustang profile</a>${p.profileUrl?`<a href="${esc(p.profileUrl)}" target="_blank" rel="noopener">Official profile ↗</a>`:''}</div></article>`}
function awardCard(a){let p=DATA.players.find(x=>String(x.mlbId)===String(a.playerId))||DATA.players.find(x=>x.name===a.player);return`<div class="award">${p?portrait(p,'award-photo'):''}<div class="award-copy"><small>${esc(a.label)}</small><strong>${esc(a.player)}</strong>${a.team?`<em>${esc(a.team)}</em>`:''}<span>${esc(a.line||'')}</span></div></div>`}
function highlightCard(h){return`<article class="highlight"><div class="video-wrap"><video controls preload="metadata" poster="${esc(h.image||'')}"><source src="${esc(h.url)}"></video></div><div><b>${esc(h.title)}</b><p>${esc(h.player)}</p><a href="${esc(h.url)}" target="_blank" rel="noopener">Open official video ↗</a></div></article>`}
function leaderCard(label,p,k){if(!p)return`<div class="leader-card untracked"><small>${label}</small><b>Not tracked yet</b></div>`;return`<div class="leader-card">${portrait(p,'leader-portrait')}<div class="leader-copy"><small>${label}</small><b>${esc(p.name)}</b><strong>${esc(p.stats[k])} ${k}</strong><span class="meta">${esc(p.recentTeam||p.team)} · ${esc(normalizedLevel(p))}</span></div>${p.teamId?`<img class="team-logo" src="${logo(p.teamId)}" alt="">`:''}</div>`}
function leaders(){let hitters=DATA.players.filter(p=>p.type==='hitter'&&p.status!=='fa'),pitchers=DATA.players.filter(p=>p.type==='pitcher'&&p.status!=='fa');let n=v=>{let x=parseFloat(String(v).replace('—',''));return Number.isFinite(x)?x:-Infinity};let best=(arr,key,min=false)=>arr.filter(p=>n(p.stats?.[key])!==-Infinity).sort((a,b)=>(min?1:-1)*(n(a.stats[key])-n(b.stats[key])))[0];$('#hitterLeaders').innerHTML=[['Highest AVG',best(hitters,'AVG'),'AVG'],['Highest OPS',best(hitters,'OPS'),'OPS'],['Most HR',best(hitters,'HR'),'HR'],['Most RBI',best(hitters,'RBI'),'RBI'],['Most SB',best(hitters,'SB'),'SB']].map(x=>leaderCard(...x)).join('');$('#pitcherLeaders').innerHTML=[['Lowest ERA',best(pitchers,'ERA',true),'ERA'],['Most Strikeouts',best(pitchers,'SO'),'SO'],['Lowest WHIP',best(pitchers,'WHIP',true),'WHIP']].map(x=>leaderCard(...x)).join('')}
function careerNumber(v){let n=parseFloat(String(v??'').replace(/,/g,''));return Number.isFinite(n)?n:-Infinity}function careerIp(v){let s=String(v??'0'),[i,f='0']=s.split('.');return(parseInt(i)||0)+((parseInt(f)||0)/3)}
function careerLeaderCard(label,p,key){if(!p)return'';return`<a class="career-leader-card" href="${esc(p.url)}" target="_blank" rel="noopener"><small>${esc(label)}</small><b>${esc(p.name)}</b><strong>${esc(p.stats?.[key]??'—')}</strong><span>${esc(p.years)} · Baseball Reference ↗</span>${p.hof?'<i class="hof-badge">HOF</i>':''}</a>`}
function careerCard(p){let keys=p.type==='hitter'?['AB','H','HR','RBI','SB','AVG','OBP','OPS']:['G','GS','W','L','SV','IP','SO','ERA','WHIP'];return`<a class="career-card" href="${esc(p.url)}" target="_blank" rel="noopener"><div class="career-card-head"><div><span class="career-type">${p.type==='hitter'?'Hitter':'Pitcher'}</span><h4>${esc(p.name)}</h4><p>${esc(p.years)}</p></div>${p.hof?'<span class="hof-badge">Hall of Fame</span>':'<span class="br-badge">B-R ↗</span>'}</div><div class="career-stats">${keys.map(k=>`<div><b>${esc(p.stats?.[k]??'—')}</b><small>${k}</small></div>`).join('')}</div></a>`}
function renderCareer(){let all=DATA.career?.players||[],hitters=all.filter(p=>p.type==='hitter'),pitchers=all.filter(p=>p.type==='pitcher');if(!all.length)return;let max=(arr,key)=>[...arr].sort((a,b)=>careerNumber(b.stats?.[key])-careerNumber(a.stats?.[key]))[0],minRate=(arr,key,minIp=200)=>[...arr].filter(p=>careerIp(p.stats?.IP)>=minIp&&careerNumber(p.stats?.[key])!==-Infinity).sort((a,b)=>careerNumber(a.stats?.[key])-careerNumber(b.stats?.[key]))[0];let bestOps=[...hitters].filter(p=>careerNumber(p.stats?.AB)>=500).sort((a,b)=>careerNumber(b.stats?.OPS)-careerNumber(a.stats?.OPS))[0];$('#careerHitterLeaders').innerHTML=[['Most Hits',max(hitters,'H'),'H'],['Most Home Runs',max(hitters,'HR'),'HR'],['Most RBI',max(hitters,'RBI'),'RBI'],['Most Stolen Bases',max(hitters,'SB'),'SB'],['Highest OPS (500+ AB)',bestOps,'OPS']].map(x=>careerLeaderCard(...x)).join('');$('#careerPitcherLeaders').innerHTML=[['Most Wins',max(pitchers,'W'),'W'],['Most Strikeouts',max(pitchers,'SO'),'SO'],['Most Saves',max(pitchers,'SV'),'SV'],['Most Games',max(pitchers,'G'),'G'],['Lowest ERA (200+ IP)',minRate(pitchers,'ERA'),'ERA'],['Lowest WHIP (200+ IP)',minRate(pitchers,'WHIP'),'WHIP']].map(x=>careerLeaderCard(...x)).join('');$('#careerHitters').innerHTML=[...hitters].sort((a,b)=>a.name.localeCompare(b.name)).map(careerCard).join('');$('#careerPitchers').innerHTML=[...pitchers].sort((a,b)=>a.name.localeCompare(b.name)).map(careerCard).join('');$('#careerCount').textContent=`${all.length} Mustangs have reached Major League Baseball`}
function renderRoster(){let q=($('#search')?.value||'').toLowerCase(),sort=$('#sort')?.value||'level';let list=DATA.players.filter(p=>!q||`${p.name} ${p.position} ${p.team} ${p.recentTeam} ${normalizedLevel(p)}`.toLowerCase().includes(q));if(sort==='name')list.sort((a,b)=>a.name.localeCompare(b.name));else if(sort==='organization')list.sort((a,b)=>(a.team||'').localeCompare(b.team||'')||a.name.localeCompare(b.name));else if(sort==='position')list.sort((a,b)=>(a.position||'').localeCompare(b.position||'')||a.name.localeCompare(b.name));else list.sort((a,b)=>levels.indexOf(normalizedLevel(a))-levels.indexOf(normalizedLevel(b))||a.name.localeCompare(b.name));let root=$('#roster');if(sort!=='level'){root.innerHTML=`<div class="grid">${list.map(playerCard).join('')}</div>`;return}root.innerHTML=levels.map(l=>{let ps=list.filter(p=>normalizedLevel(p)===l);return ps.length?`<section class="level-section"><div class="level-header" data-level="${l}"><h3>${l}</h3><span class="level-count">${ps.length} Mustang${ps.length===1?'':'s'}</span></div><div class="grid">${ps.map(playerCard).join('')}</div></section>`:''}).join('')}

function pacificTime(iso){if(!iso)return'TBD';try{return new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit'}).format(new Date(iso))+' PT'}catch{return iso}}
function probableName(p){return p?.fullName||p?.name||'TBD'}
function probableId(p){return p?.id||null}
function starterHtml(label,p){let id=probableId(p);return`<div class="starter"><span>${label}</span>${id?`<img src="${headshot(id)}" alt="${esc(probableName(p))}" onerror="this.style.display='none'">`:''}<b>${esc(probableName(p))}</b></div>`}
function gamedayUrl(g){
  if(!g?.gamePk)return '';
  let status=String(g.status||'');
  let final=/final|completed|game over/i.test(status);
  let live=/in progress|live|delayed|manager challenge|review/i.test(status);
  let domain=Number(g.sportId)===1?'https://www.mlb.com':'https://www.milb.com';
  let view=final?'final/box':live?'live':'preview';
  return `${domain}/gameday/${g.gamePk}/${view}`;
}
function gameCards(games){
  let unique=[];let seen=new Map();
  for(const g of games){
    let key=g.gamePk||`${g.away}-${g.home}-${g.gameDate}`;
    if(!seen.has(key)){let row={...g,tracked:[]};seen.set(key,row);unique.push(row)}
    seen.get(key).tracked.push(g.player)
  }
  const levelRank={"MLB":1,"Triple-A":2,"Double-A":3,"High-A":4,"Single-A":5,"Rookie":6,"Rookie Ball":6};
  unique.sort((a,b)=>{
    let ar=levelRank[a.level]??99, br=levelRank[b.level]??99;
    if(ar!==br)return ar-br;
    let at=new Date(a.gameDate||0).getTime()||0, bt=new Date(b.gameDate||0).getTime()||0;
    return at-bt;
  });
  return unique.map(g=>{
    let awayLogo=g.awayTeamId?logo(g.awayTeamId):'',homeLogo=g.homeTeamId?logo(g.homeTeamId):'';
    let status=g.status||'Scheduled';
    let live=/in progress|live/i.test(status);
    let final=/final|completed|game over/i.test(status);
    let href=gamedayUrl(g);
    let action=final?'View box score':live?'Open live Gameday':'Open game preview';
    return`${href?`<a class="matchup-link" href="${href}" target="_blank" rel="noopener" aria-label="${esc(action)}: ${esc(g.away||'Away')} at ${esc(g.home||'Home')}">`:''}<article class="matchup-card clickable-game"><div class="matchup-top"><span class="matchup-time">${g.level?`${esc(g.level)} · `:''}${live?esc(status):pacificTime(g.gameDate)}</span>${g.venue?`<span class="matchup-venue">${esc(g.venue)}</span>`:''}</div><div class="teams-row"><div class="team-side">${awayLogo?`<img src="${awayLogo}" alt="${esc(g.away)}">`:''}<b>${esc(g.away||'Away')}</b>${g.awayScore!=null?`<strong>${esc(g.awayScore)}</strong>`:''}</div><div class="at-mark">@</div><div class="team-side">${homeLogo?`<img src="${homeLogo}" alt="${esc(g.home)}">`:''}<b>${esc(g.home||'Home')}</b>${g.homeScore!=null?`<strong>${esc(g.homeScore)}</strong>`:''}</div></div><div class="starters-row">${starterHtml('Projected starter',g.awayProbablePitcher)}${starterHtml('Projected starter',g.homeProbablePitcher)}</div>${g.tracked.length?`<div class="tracked-line"><span>Mustangs:</span> ${g.tracked.map(esc).join(', ')}</div>`:''}${g.liveLine?`<div class="live-line">${esc(g.liveLine)}</div>`:''}<div class="game-card-action">${esc(action)} ↗</div></article>${href?'</a>':''}`
  }).join('')
}
function dailyResultStrip(){
  let apps=DATA.summary?.appearances||[];
  if(!apps.length)return '<div class="daily-results-empty">No tracked Mustang results from last night.</div>';
  return apps.map(a=>{
    let p=DATA.players.find(x=>String(x.mlbId)===String(a.playerId))||{mlbId:a.playerId,name:a.name,initials:(a.name||'CP').split(/\s+/).map(x=>x[0]).join('').slice(0,2)};
    let ctx=a.gameContext||{}, result=ctx.result, score=(ctx.teamFinal!=null&&ctx.opponentFinal!=null)?`${ctx.teamFinal}-${ctx.opponentFinal}`:'';
    let resultText=result==='win'?`W ${score}`:result==='loss'?`L ${score}`:score;
    let key=(ctx.keyPlays||[]).find(x=>(x.tags||[]).includes('walk-off'))||(ctx.keyPlays||[]).find(x=>(x.tags||[]).includes('go-ahead'))||(ctx.keyPlays||[]).find(x=>(x.tags||[]).includes('game-tying'));
    let moment='';
    if(key){let tags=key.tags||[], inning=key.inning?`${key.inning}${Number(key.inning)===1?'st':Number(key.inning)===2?'nd':Number(key.inning)===3?'rd':'th'}`:'';moment=tags.includes('walk-off')?`Walk-off ${key.event||'hit'}`:tags.includes('go-ahead')?`Go-ahead ${key.event||'hit'}${inning?' · '+inning:''}`:`Game-tying ${key.event||'hit'}${inning?' · '+inning:''}`}
    return `<a class="daily-result" href="player.html?id=${esc(a.playerId)}">${portrait(p,'result-portrait')}<div class="daily-result-copy"><b>${esc(a.name)}</b><span>${esc(a.summary||'')}</span>${moment?`<small>★ ${esc(moment)}</small>`:''}</div><div class="daily-result-game"><strong>${esc(resultText)}</strong><span>${esc(a.team||'')}</span></div></a>`;
  }).join('');
}
function ensureGameLinkStyles(){
  if(document.getElementById('gameLinkStyles'))return;
  let s=document.createElement('style');s.id='gameLinkStyles';
  s.textContent='.matchup-link{display:block;color:inherit;text-decoration:none}.matchup-link .matchup-card{height:100%;transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease;cursor:pointer}.matchup-link:hover .matchup-card{transform:translateY(-2px);box-shadow:0 14px 32px rgba(0,0,0,.14);border-color:rgba(196,151,31,.55)}.game-card-action{margin-top:12px;padding-top:10px;border-top:1px solid rgba(0,0,0,.09);font:700 10px \"Space Mono\";text-transform:uppercase;letter-spacing:.04em;color:var(--green)}';
  document.head.appendChild(s);
}
function arrangeDailySections(){
  let highlightsSection=$('#highlights')?.closest('section');
  let gamesSection=$('#games')?.closest('section');
  if(highlightsSection&&gamesSection&&highlightsSection!==gamesSection&&gamesSection.parentNode){
    gamesSection.parentNode.insertBefore(highlightsSection,gamesSection);
  }
}

function archiveHighlightCard(h){
  return `<article class="archive-video"><div class="archive-video-media">${h.image?`<img src="${esc(h.image)}" alt="">`:''}<span>▶</span></div><div><b>${esc(h.title||'Official highlight')}</b><small>${esc(h.player||'')}</small><a href="${esc(h.url||'#')}" target="_blank" rel="noopener">Watch official video ↗</a></div></article>`;
}
function archiveEditionCard(e,index){
  let paragraphs=e.paragraphs||[], clips=e.highlights||[];
  let excerpt=paragraphs[0]||'Mustangs Daily edition';
  return `<article class="archive-edition"><button class="archive-edition-toggle" type="button" aria-expanded="${index===0?'true':'false'}"><div><small>${esc(e.dateLabel||e.date||'')}</small><h3>${esc(e.title||'Mustangs Daily')}</h3><p>${esc(excerpt)}</p></div><span>${clips.length} video${clips.length===1?'':'s'} · ${index===0?'−':'+'}</span></button><div class="archive-edition-body" ${index===0?'':'hidden'}>${paragraphs.map(p=>`<p>${esc(p)}</p>`).join('')}${clips.length?`<div class="archive-video-grid">${clips.map(archiveHighlightCard).join('')}</div>`:'<div class="archive-no-video">No official player-tagged video was archived for this edition.</div>'}</div></article>`;
}
function ensureArchiveSection(){
  if($('#archive'))return;
  let about=$('#about')?.closest('section')||$('#about');
  let html=`<section id="archive" class="section archive-section"><div class="section-head"><div><div class="eyebrow">Mustangs Daily history</div><h2 class="section-title">Article & Highlight Archive</h2></div><span class="meta">Newest first</span></div><div class="archive-tools"><input id="archiveSearch" type="search" placeholder="Search player, team or story"><select id="archiveYear"><option value="">All years</option></select></div><div id="archiveList" class="archive-list"></div></section>`;
  if(about&&about.parentNode)about.parentNode.insertBefore(document.createRange().createContextualFragment(html),about);
  else document.querySelector('main')?.insertAdjacentHTML('beforeend',html);
  let nav=document.querySelector('header nav, nav');
  if(nav&&!nav.querySelector('a[href="#archive"]'))nav.insertAdjacentHTML('beforeend','<a href="#archive">Archive</a>');
}
function buildProMustangsMenu(){
  let nav=document.querySelector('header nav, nav');
  if(!nav)return;

  const order=['Daily','Highlights','Games','Roster','Transactions','Leaders','Career','MLB History','Archive','About'];
  const existing=[...nav.querySelectorAll('a')];
  const byLabel=new Map(existing.map(a=>[a.textContent.trim().toLowerCase(),a.getAttribute('href')]));

  // Remove the old flat links so the header becomes one clean category.
  existing.forEach(a=>{
    if(order.map(x=>x.toLowerCase()).includes(a.textContent.trim().toLowerCase()))a.remove();
  });

  let old=nav.querySelector('.pro-mustangs-menu');
  if(old)old.remove();

  let links=order.map(label=>{
    let href=byLabel.get(label.toLowerCase())||'#';
    return `<a href="${esc(href)}">${esc(label)}</a>`;
  }).join('');

  if(!nav.querySelector('a[href="slo-life.html"]'))nav.insertAdjacentHTML('beforeend','<a class="slo-life-nav-link" href="slo-life.html">SLO Life</a>');

  nav.insertAdjacentHTML('beforeend',`
    <div class="pro-mustangs-menu">
      <button class="pro-mustangs-toggle" type="button" aria-expanded="false" aria-haspopup="true">
        Pro Mustangs <span aria-hidden="true">▾</span>
      </button>
      <div class="pro-mustangs-dropdown" hidden>
        ${links}
      </div>
    </div>
  `);

  let menu=nav.querySelector('.pro-mustangs-menu');
  let button=menu.querySelector('.pro-mustangs-toggle');
  let dropdown=menu.querySelector('.pro-mustangs-dropdown');

  const close=()=>{
    dropdown.hidden=true;
    button.setAttribute('aria-expanded','false');
    menu.classList.remove('open');
  };
  const open=()=>{
    dropdown.hidden=false;
    button.setAttribute('aria-expanded','true');
    menu.classList.add('open');
  };

  button.onclick=e=>{
    e.stopPropagation();
    dropdown.hidden?open():close();
  };
  dropdown.querySelectorAll('a').forEach(a=>a.onclick=close);
  document.addEventListener('click',e=>{
    if(!menu.contains(e.target))close();
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape')close();
  });
}
function renderArchive(){
  ensureArchiveSection();
  buildProMustangsMenu();
  let editions=[...(DATA.archive?.editions||[])];
  // On first deployment, surface the current article immediately even before
  // archive.json has accumulated older nights.
  if(!editions.length&&DATA.daily?.date){
    let clips=(DATA.summary?.appearances||[]).flatMap(a=>(a.highlights||[]).map(h=>({...h,player:a.name,playerId:a.playerId})));
    editions=[{...DATA.daily,highlights:clips}];
  }
  let years=[...new Set(editions.map(e=>String(e.date||'').slice(0,4)).filter(Boolean))].sort().reverse();
  let year=$('#archiveYear');
  if(year)year.innerHTML='<option value="">All years</option>'+years.map(y=>`<option value="${esc(y)}">${esc(y)}</option>`).join('');
  let draw=()=>{
    let q=($('#archiveSearch')?.value||'').trim().toLowerCase(), y=$('#archiveYear')?.value||'';
    let rows=editions.filter(e=>{
      if(y&&String(e.date||'').slice(0,4)!==y)return false;
      if(!q)return true;
      let hay=[e.title,...(e.paragraphs||[]),...(e.appearances||[]).flatMap(a=>[a.name,a.team,a.level,a.summary])].join(' ').toLowerCase();
      return hay.includes(q);
    });
    $('#archiveList').innerHTML=rows.length?rows.map(archiveEditionCard).join(''):'<div class="empty">No archived editions match this search.</div>';
    $$('.archive-edition-toggle').forEach(btn=>btn.onclick=()=>{
      let body=btn.nextElementSibling, opening=body.hasAttribute('hidden');
      if(opening)body.removeAttribute('hidden');else body.setAttribute('hidden','');
      btn.setAttribute('aria-expanded',String(opening));
      let span=btn.querySelector(':scope > span');
      if(span)span.textContent=span.textContent.replace(/[+−]$/,opening?'−':'+');
    });
  };
  if($('#archiveSearch'))$('#archiveSearch').oninput=draw;
  if($('#archiveYear'))$('#archiveYear').onchange=draw;
  draw();
}
function heroPhotoCarousel(){
  let target=document.querySelector('.hero .scorecard');
  if(!target)return;

  // Curated action photos supplied for the site. These always lead the carousel.
  const curated=[
    {image:'assets/action/brooks-lee.jpg',player:'Brooks Lee'},
    {image:'assets/action/bryan-woo.webp',player:'Bryan Woo'},
    {image:'assets/action/drew-thorpe.jpg',player:'Drew Thorpe'},
    {image:'assets/action/andrew-alvarez.jpg',player:'Andrew Alvarez'}
  ];

  let actionPhotos=[],seen=new Set();
  const addPhoto=(image,playerName,playerId,title='',video='',dateLabel='')=>{
    if(!image||seen.has(image))return;
    seen.add(image);
    let p=DATA.players.find(x=>String(x.mlbId)===String(playerId))
      || DATA.players.find(x=>x.name===playerName);
    actionPhotos.push({
      image,
      video,
      title:title||'Mustang in Pro Ball',
      player:playerName||p?.name||'Mustang in Pro Ball',
      team:p?.recentTeam||p?.team||'',
      level:p?normalizedLevel(p):'',
      date:dateLabel||''
    });
  };

  curated.forEach(photo=>addPhoto(photo.image,photo.player,null,'Mustang in Pro Ball'));

  // After the curated photos, continue adding official game-action imagery from
  // current and archived MLB/MiLB highlights so the carousel can grow over time.
  (DATA.summary?.appearances||[]).forEach(a=>{
    (a.highlights||[]).forEach(h=>addPhoto(h.image,a.name,a.playerId,h.title,h.url,DATA.daily?.dateLabel||DATA.summary?.date));
  });
  (DATA.archive?.editions||[]).forEach(e=>{
    (e.highlights||[]).forEach(h=>addPhoto(h.image,h.player,h.playerId,h.title,h.url,e.dateLabel||e.date));
  });

  actionPhotos=actionPhotos.slice(0,20);
  if(!actionPhotos.length){target.remove();return}

  let slides=actionPhotos.map((photo,i)=>{
    let caption=[photo.team,photo.level].filter(Boolean).join(' · ');
    let figure=`<figure class="hero-player-slide hero-action-slide ${i===0?'active':''}" data-index="${i}"><img src="${esc(photo.image)}" alt="${esc(photo.player)} game action"><figcaption><small>${esc(photo.date||'Mustang in Pro Ball')}</small><strong>${esc(photo.player)}</strong>${caption?`<span>${esc(caption)}</span>`:''}</figcaption></figure>`;
    return photo.video?`<a class="hero-action-link" href="${esc(photo.video)}" target="_blank" rel="noopener">${figure}</a>`:figure;
  }).join('');

  target.outerHTML=`<div class="hero-photo-feature" id="heroPhotoFeature"><div class="hero-photo-stage">${slides}</div><button class="hero-photo-nav prev" type="button" aria-label="Previous action photo">‹</button><button class="hero-photo-nav next" type="button" aria-label="Next action photo">›</button><div class="hero-photo-count"><span id="heroPhotoCurrent">1</span> / ${actionPhotos.length}</div></div>`;

  let root=$('#heroPhotoFeature'),slideEls=[...root.querySelectorAll('.hero-player-slide')],current=0,timer;
  const show=n=>{current=(n+slideEls.length)%slideEls.length;slideEls.forEach((el,i)=>el.classList.toggle('active',i===current));let c=$('#heroPhotoCurrent');if(c)c.textContent=current+1};
  const restart=()=>{clearInterval(timer);timer=setInterval(()=>show(current+1),5500)};
  root.querySelector('.next').onclick=e=>{e.preventDefault();e.stopPropagation();show(current+1);restart()};
  root.querySelector('.prev').onclick=e=>{e.preventDefault();e.stopPropagation();show(current-1);restart()};
  root.addEventListener('mouseenter',()=>clearInterval(timer));
  root.addEventListener('mouseleave',restart);
  restart();
}
function renderHome(){ensureGameLinkStyles();heroPhotoCarousel();arrangeDailySections();let dailySection=$('#dailyBody')?.closest('.daily');if(dailySection&&!$('#dailyResultsWrap'))dailySection.insertAdjacentHTML('afterend','<section id="dailyResultsWrap" class="daily-results-wrap"><div class="daily-results-head"><div><span>LAST NIGHT</span><h3>Daily Results</h3></div><small>Click a player for full profile</small></div><div id="dailyResults" class="daily-results-strip"></div></section>');let article=DATA.daily||{};$('#dailyTitle').textContent=article.title||'Mustangs Daily';$('#dailyDate').textContent=article.dateLabel||DATA.summary?.date||'Latest report';$('#dailyBody').innerHTML=(article.paragraphs||['The next morning edition will publish the latest Mustangs Daily report.']).map(p=>`<p>${esc(p)}</p>`).join('');let results=$('#dailyResults');if(results)results.innerHTML=dailyResultStrip();let awards=article.awards||[];$('#awards').innerHTML=awards.length?awards.map(awardCard).join(''):'<div class="empty">Awards appear after tracked players compete.</div>';let clips=(DATA.summary?.appearances||[]).flatMap(a=>(a.highlights||[]).map(h=>({...h,player:a.name})));$('#highlights').innerHTML=clips.length?clips.map(highlightCard).join(''):'<div class="empty">Official highlights will appear when MLB or MiLB publishes player-tagged video.</div>';let games=DATA.schedule?.games||[];$('#games').innerHTML=games.length?gameCards(games):'<div class="empty">No tracked games were found on today’s schedule.</div>';let tx=DATA.transactions?.transactions||[];$('#transactions').innerHTML=tx.length?tx.map(t=>`<div class="transaction-card"><span class="tx-icon">↕</span><div><small>Roster move</small><b>${esc(t.player)}</b><p>${esc(t.description)}</p><span>${esc(t.date||'')}</span></div></div>`).join(''):'<div class="empty">No recent tracked transactions.</div>';renderRoster();leaders();renderCareer();renderArchive()}

function recentOrders(p){return p.type==='hitter'?['G','AB','R','H','2B','3B','HR','RBI','BB','SO','SB','AVG','OBP','SLG','OPS']:['G','IP','H','R','ER','BB','SO','ERA','WHIP']}
function shortDate(v){if(!v)return'';let d=new Date(v+'T12:00:00');if(Number.isNaN(d.getTime()))return v;return new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',timeZone:'America/Los_Angeles'}).format(d)}
function lastSevenSection(p){
  let recent=p.last7||{}, st=recent.stats||{}, games=Number(recent.games||0);
  let range=recent.startDate&&recent.endDate?`${shortDate(recent.startDate)} – ${shortDate(recent.endDate)}`:'Rolling seven-game window';
  if(!games)return `<section class="section recent-section"><div class="section-head"><div><div class="eyebrow">Recent form</div><h2 class="section-title">Last 7 Games</h2></div><span class="meta">Waiting for game logs</span></div><div class="empty">Seven recent professional appearances are not available yet for ${esc(p.name)}.</div></section>`;
  return `<section class="section recent-section"><div class="section-head"><div><div class="eyebrow">Recent form</div><h2 class="section-title">Last 7 Games</h2></div><div class="recent-meta"><b>${games} most recent appearance${games===1?'':'s'}</b><span>${esc(range)}</span></div></div><div class="stats recent-stats ${p.type==='pitcher'?'pitcher':''}">${recentOrders(p).map(k=>`<div class="stat"><b>${esc(st[k]??'—')}</b><small>${k}</small></div>`).join('')}</div><p class="recent-note">Rolling totals from ${games===7?'the seven most recent professional games/appearances':`the ${games} available professional appearance${games===1?'':'s'}`} — not the last seven calendar days.</p></section>`;
}
function renderPlayer(){let id=new URLSearchParams(location.search).get('id'),p=DATA.players.find(x=>String(x.mlbId)===String(id));if(!p){$('#playerRoot').innerHTML='<div class="empty">Player not found.</div>';return}document.title=`${p.name} — Mustangs in Pro Ball`;$('#playerRoot').innerHTML=`<section class="hero"><div class="shell player-hero">${portrait(p)}<div><div class="eyebrow">Mustang profile · ${esc(normalizedLevel(p))}</div><h1>${esc(p.name)}</h1><p class="lede">${esc(p.position)} · ${esc(p.recentTeam||p.team)}<br>${esc(p.drafted||'')}</p>${p.profileUrl?`<a class="hero-link" href="${esc(p.profileUrl)}" target="_blank" rel="noopener">Official player profile ↗</a>`:''}</div></div></section><main class="shell"><section class="section"><div class="section-head"><h2 class="section-title">2026 Season</h2><span class="meta">Updated automatically</span></div><div class="stats ${p.type==='pitcher'?'pitcher':''} player-page-stats">${orders(p).map(k=>`<div class="stat"><b>${esc(p.stats?.[k]??'—')}</b><small>${k}</small></div>`).join('')}</div></section>${lastSevenSection(p)}<section class="section daily"><div class="article-card"><div class="eyebrow">Player story</div><h2>${esc(p.name)}</h2><div class="article-body"><p>${esc(p.note||'')}</p></div></div><div class="panel current-panel"><div class="eyebrow">Current assignment</div><h3>${esc(p.recentTeam||p.team)}</h3><p>${esc(normalizedLevel(p))}</p><hr><div class="eyebrow">Draft</div><p>${esc(p.drafted||'')}</p></div></section></main>`}
function setupNav(){const header=$('#siteHeader'),toggle=$('#menuToggle'),nav=$('#primaryNav');toggle?.addEventListener('click',()=>{let open=nav.classList.toggle('open');toggle.setAttribute('aria-expanded',String(open))});nav?.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{nav.classList.remove('open');toggle?.setAttribute('aria-expanded','false')}));addEventListener('scroll',()=>header?.classList.toggle('is-scrolled',scrollY>24),{passive:true})}
function formatPacificStamp(value){if(!value)return '—';let d=new Date(value);if(Number.isNaN(d.getTime()))return String(value);return new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',month:'long',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true,timeZoneName:'short'}).format(d)}
function updateHeroMeta(){let mlb=DATA.players.filter(p=>normalizedLevel(p)==='MLB').length,orgs=new Set(DATA.players.filter(p=>p.status!=='fa').map(p=>p.team).filter(Boolean)).size;let countPlayers=$('#countPlayers'),countMlb=$('#countMlb'),countOrgs=$('#countOrgs');if(countPlayers)countPlayers.textContent=DATA.players.length;if(countMlb)countMlb.textContent=mlb;if(countOrgs)countOrgs.textContent=orgs;let stamp=DATA.statsMeta?.updatedAt||DATA.schedule?.generatedAt||DATA.summary?.generatedAt||DATA.daily?.generatedAt||DATA.summary?.date;let pretty=formatPacificStamp(stamp);let hero=$('#heroUpdated'),about=$('#aboutUpdated');if(hero)hero.textContent='Last refreshed: '+pretty;if(about)about.textContent='Last refresh: '+pretty}
document.addEventListener('DOMContentLoaded',async()=>{await loadAll();if(document.body.dataset.page==='player')renderPlayer();else{renderHome();updateHeroMeta();setupNav();$('#search')?.addEventListener('input',renderRoster);$('#sort')?.addEventListener('change',renderRoster)}});
