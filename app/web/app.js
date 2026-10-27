const cats=[
  {id:'files',icon:'▱',title:'Fichiers personnels',desc:'Documents, images, vidéos, musique, bureau, téléchargements',tone:''},
  {id:'browsers',icon:'◎',title:'Navigateurs',desc:'Favoris, historique et profils locaux',tone:''},
  {id:'licenses',icon:'⚿',title:'Licences',desc:'Clé Windows OEM et licences logicielles',tone:'purple'},
  {id:'programs',icon:'▦',title:'Logiciels',desc:'Liste des programmes installés, à réinstaller avec Winget',tone:'green'},
  {id:'email',icon:'✉',title:'Messagerie',desc:'Données locales Outlook et Thunderbird',tone:'pink'},
  {id:'wifi',icon:'◉',title:'Wi-Fi et pilotes',desc:'Profils réseau et pilotes système',tone:''}
];
let state=null,licenses=[],poll=null,noticeTimer=null;
const $=s=>document.querySelector(s);
let pendingConfirmation=null;
function confirmAction({title,message,acceptLabel='Continuer'}){
  if(pendingConfirmation)return Promise.resolve(false);
  const dialog=$('#confirmation-dialog');
  const trigger=document.activeElement;
  $('#confirmation-title').textContent=title;
  $('#confirmation-message').textContent=message;
  $('#confirmation-accept').textContent=acceptLabel;
  return new Promise(resolve=>{
    pendingConfirmation={resolve,trigger};
    document.body.classList.add('confirmation-open');
    dialog.showModal();
    $('#confirmation-cancel').focus({preventScroll:true});
  });
}
function closeConfirmation(accepted=false){
  if(!pendingConfirmation)return;
  // Save the answer on this dialog; it never starts an operation by itself.
  $('#confirmation-dialog').close(accepted?'confirmed':'cancelled');
}
$('#confirmation-dialog').addEventListener('close',()=>{
  const pending=pendingConfirmation;
  if(!pending)return;
  pendingConfirmation=null;
  document.body.classList.remove('confirmation-open');
  const trigger=pending.trigger?.isConnected?pending.trigger:document.querySelector('.nav.active');
  trigger?.focus({preventScroll:true});
  pending.resolve($('#confirmation-dialog').returnValue==='confirmed');
});
$('#confirmation-dialog').addEventListener('cancel',e=>{e.preventDefault();closeConfirmation(false)});
$('#confirmation-dialog').addEventListener('keydown',e=>{
  if(e.key!=='Tab')return;
  const buttons=[...e.currentTarget.querySelectorAll('button:not(:disabled)')];
  const first=buttons[0],last=buttons[buttons.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
});
$('#confirmation-close').addEventListener('click',()=>closeConfirmation(false));
$('#confirmation-cancel').addEventListener('click',()=>closeConfirmation(false));
$('#confirmation-accept').addEventListener('click',()=>closeConfirmation(true));
const svg=(body)=>`<svg viewBox="0 0 36 36" width="38" height="38" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const cardIcons={files:svg('<path d="M4 10h10l3 4h15v16H4z"/><path d="M4 14V8h11l3 4"/>'),browsers:svg('<circle cx="18" cy="18" r="14"/><path d="M4 18h28M18 4c-8 8-8 20 0 28M18 4c8 8 8 20 0 28"/>'),licenses:svg('<circle cx="13" cy="13" r="9"/><path d="m19 20 12 12M24 25l-5 5"/><circle cx="11" cy="11" r="1"/>'),programs:svg('<rect x="5" y="5" width="10" height="10" rx="1"/><rect x="21" y="5" width="10" height="10" rx="1"/><rect x="5" y="21" width="10" height="10" rx="1"/><rect x="21" y="21" width="10" height="10" rx="1"/>'),email:svg('<rect x="3" y="7" width="30" height="22" rx="2"/><path d="m3 9 15 12L33 9"/>'),wifi:svg('<path d="M3 14c9-9 21-9 30 0M8 19c6-6 14-6 20 0M13 24c3-3 7-3 10 0"/><circle cx="18" cy="29" r="2" fill="currentColor"/>')};
const navIcons={home:svg('<path d="m4 17 14-12 14 12M8 16v15h8V22h5v9h8V16" fill="currentColor" stroke="none"/>'),backups:svg('<circle cx="18" cy="18" r="14"/><path d="M18 9v10l7 4"/>'),restore:svg('<path d="M7 13a13 13 0 1 1-2 10M7 5v8h8M7 13a13 13 0 0 1 10-8"/>'),destination:svg('<ellipse cx="18" cy="8" rx="12" ry="5"/><path d="M6 8v20c0 7 24 7 24 0V8M6 18c0 7 24 7 24 0"/>'),settings:svg('<circle cx="18" cy="18" r="5"/><path d="m16 3-1 4-4 2-4-2-4 7 3 3v3l-3 3 4 7 4-2 4 2 1 4h5l1-4 4-2 4 2 4-7-3-3v-3l3-3-4-7-4 2-4-2-1-4z"/>'),tools:svg('<path d="M29 4a9 9 0 0 0-10 10L5 28a3 3 0 0 0 4 4l14-14A9 9 0 0 0 32 7l-6 6-5-2-2-5z"/>')};
Object.assign(navIcons,{files:cardIcons.files,browsers:cardIcons.browsers,email:cardIcons.email,licenses:cardIcons.licenses,wifi:cardIcons.wifi,programs:cardIcons.programs,checklist:svg('<path d="m6 18 8 8L30 9"/>')});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=()=>window.pywebview?.api;
function formatDate(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?value:d.toLocaleString('fr-BE')}
// The state is polled every second: only touch the DOM when the markup actually changed,
// so focus, hover, open selects and text selections survive the refresh.
const rendered=new Map();
function setHTML(sel,html){const el=typeof sel==='string'?$(sel):sel;if(!el||rendered.get(el)===html)return;rendered.set(el,html);el.innerHTML=html}
function setText(sel,text){const el=$(sel);if(el&&el.textContent!==String(text))el.textContent=text}
function setValue(sel,value){const el=$(sel);if(el&&document.activeElement!==el&&String(el.value)!==String(value))el.value=value}
function setChecked(sel,value){const el=$(sel);if(el&&el.checked!==Boolean(value))el.checked=Boolean(value)}
const restoreLabels={files:'Fichiers personnels',browsers:'Navigateurs (profils complets)',email:'Messagerie (Thunderbird, Outlook .pst, signatures)',programs:'Programmes (réinstallation winget, Internet requis)',wifi:'Profils Wi-Fi',drivers:'Pilotes (administrateur requis)'};
let restoreSelection=null,restoreSelectionFor='';
function categoryDetail(c,s){const size=s.estimate?.per_category?.[c.id];if(size&&['files','browsers','email'].includes(c.id))return `${size.size} · ${size.files} fichiers`;switch(c.id){case 'files':return `${s.selected_folders.length} dossiers standards sélectionnés`;case 'browsers':return `${Object.values(s.detected_browsers).filter(Boolean).length} navigateur(s) détecté(s)`;case 'licenses':return 'Clé Windows et licences sauvegardées';case 'programs':return 'Inventaire des logiciels';case 'email':return 'Outlook et Thunderbird';case 'wifi':return s.admin?'Export disponible':'Droits administrateur requis';default:return ''}}
function notice(message,error=false){const el=$('#notice');el.textContent=message;el.className='show'+(error?' error':'');clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>el.className='',6000)}
async function invoke(method,...args){try{return await api()[method](...args)}catch(e){notice(String(e),true);return null}}
// Four entries in the sidebar; every other page is a sub-page opened from one of them.
// The sidebar keeps the entry that opened it highlighted, and "← Retour" goes back to it.
const mainPages=['home','restore','tools','settings'];
let currentMain='home';
function showPage(page){if(mainPages.includes(page))currentMain=page;document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id==='page-'+page));document.body.classList.toggle('home-view',page==='home');document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.page===currentMain));if(page==='licenses')loadLicenses();$('.main').scrollTo(0,0)}
const tools=[
  {page:'software',icon:'programs',tone:'green',title:'Sauvegarder un logiciel',desc:'Copier les paramètres d’un logiciel précis et préparer sa réinstallation'},
  {page:'licenses',icon:'licenses',tone:'purple',title:'Clés et licences',desc:'Retrouver la clé Windows OEM et noter vos clés logicielles'},
  {page:'destination',icon:'destination',tone:'',title:'Vérifier une sauvegarde',desc:'Relire une version et contrôler chaque fichier (SHA-256)'},
  {page:'checklist',icon:'checklist',tone:'pink',title:'Checklist avant réinstallation',desc:'Les étapes à valider avant de réinstaller Windows ou de changer de PC'}
];
function render(s){state=s;const dest=s.destination||'';
  setText('#drive-title',dest?dest.split(/[\\/]/).filter(Boolean).pop()||dest:'Aucune destination choisie');setText('#drive-subtitle',dest?(s.destination_exists?'Dossier disponible':'Dossier inaccessible (disque débranché ?)'):'Sélectionnez un disque ou un dossier');setText('#drive-path',dest||'—');setText('#drive-status',s.destination_exists?'Disponible':dest?'À vérifier':'À choisir');$('#drive-status').classList.toggle('missing',!s.destination_exists);setText('#drive-date',formatDate(s.last_backup_at));setText('#drive-free',s.capacity_free||'—');$('#capacity-fill').style.width=(s.capacity_percent||0)+'%';setText('#destination-page-path',dest||'Aucune destination choisie.');setText('#last-backup',s.last_backup_at?'Dernière sauvegarde : '+formatDate(s.last_backup_at):'Aucune sauvegarde enregistrée');setText('#status-title',s.busy?s.task+' en cours':s.last_backup_at?'Sauvegarde effectuée':'Prêt à sauvegarder');setText('#status-detail',s.busy?s.progress_label:dest?'Vos données sont prêtes pour une nouvelle sauvegarde.':'Choisissez une destination pour commencer.');setText('#status-symbol',s.busy?'↻':s.last_backup_at?'✓':'＋');$('#status-ring').classList.toggle('waiting',!s.last_backup_at);
  setText('#task-label',s.busy?s.task+' · '+s.progress_label:'Aucune opération en cours');setText('#progress-value',s.busy?s.progress+' %':'');$('#progress-fill').style.width=s.busy?s.progress+'%':'0%';setText('#progress-detail',s.busy?s.progress_detail||'':'');setText('#activity-indicator',s.busy?'· en cours':'');
  const cancel=$('#cancel-operation');cancel.hidden=!s.cancelable;cancel.disabled=s.cancel_requested;setText('#cancel-operation',s.cancel_requested?'Annulation…':'Annuler');
  document.querySelectorAll('[data-action^="backup-"],[data-action="estimate"],[data-action="restore-full"]').forEach(b=>b.disabled=s.busy);
  setText('#admin-state',s.admin?'Droits administrateur actifs.':'Droits administrateur requis pour les exports Wi-Fi et pilotes, et pour installer des pilotes.');setText('#settings-admin',s.admin?'L’application fonctionne avec les droits administrateur.':'L’application fonctionne sans droits administrateur : Wi-Fi et pilotes sont ignorés.');document.querySelectorAll('[data-admin-button]').forEach(b=>{b.hidden=s.admin;b.disabled=s.busy});
  if(s.busy)$('#activity').classList.add('open');
  renderDestination(s);renderEstimate(s);renderSettings(s);renderRestore(s);
  setHTML('#category-grid',cats.map(c=>`<label class="glass category ${c.tone}"><span class="cat-icon">${cardIcons[c.id]}</span><span class="cat-copy"><strong>${c.title}</strong><span>${c.desc}</span><small>${esc(categoryDetail(c,s))}</small><button type="button" class="text-button cat-settings" data-page="${c.id}">Régler &nbsp;→</button></span><input class="cat-check" type="checkbox" data-category="${c.id}" ${s.selected_categories.includes(c.id)?'checked':''} aria-label="Inclure ${c.title}"></label>`).join(''));
  setHTML('#folder-choices',['Bureau','Documents','Images','Téléchargements','Vidéos','Musique'].map(n=>`<label class="choice"><input type="checkbox" data-folder="${n}" ${s.selected_folders.includes(n)?'checked':''}> ${n}</label>`).join(''));
  setHTML('#custom-folders',s.custom_folders.length?s.custom_folders.map(p=>`<div class="item"><span>${esc(p)}</span><button class="secondary" data-remove-folder="${esc(p)}">Retirer</button></div>`).join(''):'<p class="hint">Aucun dossier personnalisé ajouté.</p>');
  setHTML('#browser-list',Object.entries(s.detected_browsers).map(([n,found])=>`<div class="item"><span>${esc(n)}</span><span>${found?'Détecté':'Non détecté'}</span></div>`).join(''));
  const checklist=s.checklist_items.map((item,i)=>`<label class="item"><span><input type="checkbox" data-check="${i}" ${s.checklist[item]?'checked':''}> ${esc(item)}</span></label>`).join('');
  setHTML('#checklist',checklist);
  renderSoftwareState(s);
  renderUpdates(s);
  const log=$('#log'),text=s.events.map(e=>e.message).join('\n');if(log.textContent!==text){const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<30;log.textContent=text;if(atBottom)log.scrollTop=log.scrollHeight}
}
function renderDestination(s){
  const info=s.destination_info||{};
  const warning=info.same_disk?'Attention : cette destination est sur le même disque physique que Windows. Si ce disque tombe en panne ou est effacé lors de la réinstallation, la sauvegarde sera perdue. Utilisez un disque externe, une clé USB ou un partage réseau.':'';
  ['#drive-warning','#destination-warning'].forEach(sel=>{setText(sel,warning);$(sel).hidden=!warning});
  setText('#destination-details',s.destination_exists?`Système de fichiers : ${info.filesystem||'inconnu'} · ${info.hard_links?'versions incrémentales (seules les nouveautés sont copiées)':'chaque version est une copie complète (support sans liens physiques)'}`:'');
  setValue('#keep-versions',s.settings.keep_versions);
  setHTML('#version-list',s.versions.length?s.versions.map(v=>`<div class="item version-item"><span><strong>${esc(v.legacy?'Ancienne sauvegarde (version 1.10 ou antérieure)':formatDate(v.created))}</strong> · ${esc(v.status_label)}${v.computer?` · ${esc(v.computer)}`:''}<br><small class="hint">${v.legacy?esc(v.folder):`${v.files} fichiers · ${esc(v.size)} · ${esc(v.copied)} copiés · ${esc(v.categories.join(', '))}`}</small></span><span class="version-actions"><button class="secondary" data-open-folder="${esc(v.folder)}">Ouvrir</button>${v.legacy?'':`<button class="secondary" data-verify-version="${esc(v.folder)}" ${s.busy?'disabled':''}>Vérifier</button>`}</span></div>`).join(''):`<p class="hint">${s.destination_exists?'Aucune version dans cette destination.':'Destination indisponible.'}</p>`);
  document.querySelectorAll('[data-verify-version]').forEach(b=>b.disabled=s.busy);
}
function renderEstimate(s){
  const e=s.estimate;let text='Taille de la sauvegarde non estimée.';
  if(e){const skipped=e.skipped||{};const notes=[];if(skipped.cloud)notes.push(`${skipped.cloud} fichier(s) OneDrive en ligne ignoré(s)`);if(skipped.excluded||skipped.large)notes.push(`${(skipped.excluded||0)+(skipped.large||0)} exclusion(s)`);
    text=`${e.total} sélectionnés · ${e.to_copy} à copier · ${e.free} libres${e.enough_space?'':' — espace insuffisant !'}${notes.length?' · '+notes.join(', '):''}`}
  setText('#estimate-line',text);$('#estimate-line').classList.toggle('error-text',!!e&&!e.enough_space);
}
function renderSettings(s){
  const st=s.settings;
  setHTML('#exclusion-list',st.exclusions.length?st.exclusions.map(p=>`<span class="chip">${esc(p)}<button data-remove-exclusion="${esc(p)}" aria-label="Retirer ${esc(p)}">×</button></span>`).join(''):'<p class="hint">Aucune exclusion.</p>');
  setValue('#max-size',st.max_file_size_mb);
  document.querySelectorAll('input[name="onedrive"]').forEach(r=>{if(r.checked!==(r.value===st.onedrive_cloud))r.checked=r.value===st.onedrive_cloud});
  setChecked('#verify-after-copy',st.verify_after_copy);
  setChecked('#auto-enabled',st.auto_backup.enabled);setValue('#auto-interval',st.auto_backup.interval_days||7);
  const a=st.auto_status||{};let status=st.auto_backup.enabled?'Activée.':'Désactivée.';
  if(a.last_success)status+=` Dernière sauvegarde automatique : ${formatDate(a.last_success)}${a.last_result==='partial'?' (avec des points à vérifier)':''}.`;
  if(a.last_result==='error')status+=` Dernier essai en échec : ${a.last_message||'erreur inconnue'}`;
  setText('#auto-status',status);
}
function renderRestore(s){
  setText('#restore-path',s.restore_source||'Aucun dossier choisi. Choisissez le disque ou dossier de sauvegarde, ou directement une version.');
  $('#restore-version-field').hidden=s.restore_versions.length<2;
  setHTML('#restore-version',s.restore_versions.map(v=>`<option value="${esc(v.folder)}" ${v.folder===s.restore_version?'selected':''}>${esc(v.legacy?'Ancienne sauvegarde':formatDate(v.created)+' · '+v.computer+' · '+v.status_label)}</option>`).join(''));
  if(restoreSelectionFor!==s.restore_version){restoreSelectionFor=s.restore_version;restoreSelection=new Set(s.restore_categories.filter(c=>!['drivers','programs'].includes(c)))}
  setHTML('#restore-categories',s.restore_version?(s.restore_categories.length?s.restore_categories.map(c=>`<label class="choice"><input type="checkbox" data-restore-category="${c}" ${restoreSelection.has(c)?'checked':''}> ${esc(restoreLabels[c]||c)}</label>`).join(''):'<p class="hint">Rien à restaurer dans ce dossier.</p>'):'<p class="hint">Choisissez d’abord un dossier de sauvegarde.</p>');
  $('#open-licenses').hidden=!s.restore_version;
  setHTML('#restore-list',s.restore_browsers.map(n=>`<div class="item"><span>${esc(n)}</span><button class="secondary" data-restore="${esc(n)}" ${s.busy?'disabled':''}>Restaurer</button></div>`).join('')||(s.restore_source?'<p class="hint">Aucun profil de navigateur trouvé dans ce dossier.</p>':''));
}
async function refresh(){const result=await invoke('get_state');if(result)render(result)}
async function loadLicenses(){try{licenses=await api().get_licenses();renderLicenses()}catch(e){licenses=[];renderLicenses()}}
function renderLicenses(){$('#license-list').innerHTML=licenses.length?licenses.map((l,i)=>`<div class="item"><span><strong>${esc(l.logiciel)}</strong> &nbsp; <span class="hint">${esc(l.cle)}</span></span><button class="secondary" data-license-remove="${i}">Retirer</button></div>`).join(''):'<p class="hint">Aucune licence enregistrée dans la destination actuelle.</p>'}
async function start(category){if(state?.destination_info?.same_disk&&!await confirmAction({title:'Destination sur le disque de Windows',message:'La destination est sur le même disque physique que Windows. Une panne de ce disque ou sa réinitialisation pendant la réinstallation effacerait aussi la sauvegarde.\n\nContinuer quand même ?',acceptLabel:'Sauvegarder quand même'}))return;const result=await invoke('start_backup',category);if(result&&!result.ok)notice(result.error,true);else if(result){notice('Opération démarrée. Suivez sa progression dans Activité.');await refresh()}}
async function onAction(action){if(action==='software-list'){await loadSoftware();return}if(action==='software-backup'){if(await confirmAction({title:state?.software_plan?.program?.name?`Sauvegarder ${state.software_plan.program.name} ?`:'Sauvegarder le logiciel ?',message:'Fermez le logiciel avant de continuer.\n\nLes fichiers et paramètres affichés seront copiés dans votre dossier de sauvegarde, avec l’installeur si vous en avez ajouté un.',acceptLabel:'Sauvegarder'}))await softwareOperation('backup_software');return}if(action==='choose-destination'||action==='choose-restore'||action==='add-folder'){const method=({'choose-destination':'choose_destination','choose-restore':'choose_restore_source','add-folder':'add_custom_folder'})[action];const result=await invoke(method);if(result)render(result);return}
  if(action.startsWith('backup-')){await start(action.substring(7));return}
  if(action==='windows-key'){notice('Recherche de la clé Windows…');const key=await invoke('read_windows_key');$('#windows-key-result').textContent=key||'Aucune clé OEM trouvée. Une licence numérique liée au compte Microsoft est possible.';return}
  if(action==='add-license'){const name=$('#license-name').value.trim(),key=$('#license-key').value.trim();if(!name||!key){notice('Saisissez un nom et une clé.',true);return}licenses.push({logiciel:name,cle:key});$('#license-name').value='';$('#license-key').value='';renderLicenses();notice('Licence ajoutée à la liste. Cliquez sur Enregistrer les licences.');return}
  if(action==='save-licenses'){const result=await invoke('save_licenses',licenses);if(result){licenses=result;notice('Licences enregistrées dans la destination.')}return}
  if(action==='write-report'){const result=await invoke('write_report');if(result)notice('Rapport créé : '+result);return}
  if(action==='estimate'){await operation('estimate_backup','Estimation démarrée : analyse des fichiers sélectionnés…');return}
  if(action==='add-exclusion'){const input=$('#exclusion-input');const s=await invoke('add_exclusion',input.value);if(s){input.value='';render(s)}return}
  if(action==='relaunch-admin'){if(await confirmAction({title:'Relancer en administrateur ?',message:'L’application va se fermer puis se rouvrir avec les droits administrateur après confirmation de Windows.',acceptLabel:'Relancer'})){const r=await invoke('relaunch_admin');if(r&&!r.ok)notice(r.error,true)}return}
  if(action==='open-licenses'){const r=await invoke('open_restore_licenses');if(r&&!r.ok)notice(r.error,true);return}
  if(action==='restore-full'){const chosen=[...(restoreSelection||[])].filter(c=>state.restore_categories.includes(c));if(!chosen.length){notice('Cochez au moins un élément à restaurer.',true);return}
    const message=`Éléments : ${chosen.map(c=>restoreLabels[c]).join(', ')}.\n\nLes fichiers différents déjà présents sur ce PC sont conservés sous un autre nom, rien n’est supprimé. Fermez les navigateurs et la messagerie avant de continuer.${chosen.includes('programs')?'\n\nLa réinstallation des programmes peut durer longtemps et demander des confirmations Windows.':''}`;
    if(await confirmAction({title:'Restaurer cette sauvegarde sur ce PC ?',message,acceptLabel:'Restaurer'}))await operation('restore_full','Restauration démarrée. Suivez sa progression dans Activité.',chosen);return}
}
async function operation(method,started,...args){const result=await invoke(method,...args);if(result&&!result.ok)notice(result.error,true);else if(result){notice(started);await refresh()}}
document.addEventListener('click',async e=>{const page=e.target.closest('[data-page]');if(page){e.preventDefault();showPage(page.dataset.page);return}if(e.target.closest('[data-back]')){showPage(currentMain);return}const action=e.target.closest('[data-action]');if(action){await onAction(action.dataset.action);return}const remove=e.target.closest('[data-remove-folder]');if(remove){const result=await invoke('remove_custom_folder',remove.dataset.removeFolder);if(result)render(result);return}const restore=e.target.closest('[data-restore]');if(restore){const browser=restore.dataset.restore;if(!await confirmAction({title:'Restaurer '+browser+' ?',message:'Fermez '+browser+' avant de restaurer son profil.',acceptLabel:'Restaurer'}))return;const result=await invoke('restore_browser',browser);if(result&&!result.ok)notice(result.error,true);else notice('Restauration démarrée.');return}const lic=e.target.closest('[data-license-remove]');if(lic){licenses.splice(Number(lic.dataset.licenseRemove),1);renderLicenses();notice('Licence retirée de la liste. Enregistrez pour appliquer.');return}
  const excl=e.target.closest('[data-remove-exclusion]');if(excl){const s=await invoke('remove_exclusion',excl.dataset.removeExclusion);if(s)render(s);return}
  const open=e.target.closest('[data-open-folder]');if(open){await invoke('open_folder',open.dataset.openFolder);return}
  const verify=e.target.closest('[data-verify-version]');if(verify){if(await confirmAction({title:'Vérifier cette version ?',message:'Chaque fichier est relu sur le support et comparé à son empreinte SHA-256. Cela peut prendre du temps sur une grosse sauvegarde.',acceptLabel:'Vérifier'}))await operation('verify_version','Vérification démarrée.',verify.dataset.verifyVersion)}});
document.addEventListener('change',async e=>{const t=e.target;let s=null;
  if(t.dataset.category)s=await invoke('set_selection','category',t.dataset.category,t.checked);
  else if(t.dataset.folder)s=await invoke('set_selection','folder',t.dataset.folder,t.checked);
  else if(t.dataset.check)s=await invoke('set_checklist',Number(t.dataset.check),t.checked);
  else if(t.dataset.restoreCategory){t.checked?restoreSelection.add(t.dataset.restoreCategory):restoreSelection.delete(t.dataset.restoreCategory);return}
  else if(t.id==='keep-versions')s=await invoke('set_setting','keep_versions',Number(t.value));
  else if(t.id==='max-size')s=await invoke('set_setting','max_file_size_mb',Number(t.value||0));
  else if(t.name==='onedrive')s=await invoke('set_setting','onedrive_cloud',t.value);
  else if(t.id==='verify-after-copy')s=await invoke('set_setting','verify_after_copy',t.checked);
  else if(t.id==='auto-enabled'||t.id==='auto-interval'){s=await invoke('set_auto_backup',$('#auto-enabled').checked,Number($('#auto-interval').value));if(s)notice($('#auto-enabled').checked?'Sauvegarde automatique activée.':'Sauvegarde automatique désactivée.')}
  else if(t.id==='restore-version')s=await invoke('set_restore_version',t.value);
  if(s)render(s);else if(state)render(state)});
$('#exclusion-input').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();onAction('add-exclusion')}});
$('#cancel-operation').addEventListener('click',async()=>{const r=await invoke('cancel_operation');if(r&&!r.ok)notice(r.error,true);else await refresh()});
$('#toggle-log').addEventListener('click',()=>{const el=$('#log');el.classList.toggle('open');$('#toggle-log').textContent=el.classList.contains('open')?'Masquer le journal':'Afficher le journal'});
$('#activity-toggle').addEventListener('click',()=>$('#activity').classList.toggle('open'));
window.addEventListener('pywebviewready',()=>{refresh();poll=setInterval(refresh,1100);invoke('check_updates',false);setInterval(()=>invoke('check_updates',false),6*60*60*1000)});
document.querySelectorAll('.nav').forEach(button=>{button.querySelector('span').innerHTML=navIcons[button.dataset.page]||''});
$('#tools-grid').innerHTML=tools.map(t=>`<button class="glass category hub-card ${t.tone}" data-page="${t.page}"><span class="cat-icon">${navIcons[t.icon]}</span><span class="cat-copy"><strong>${t.title}</strong><span>${t.desc}</span></span><span class="hub-arrow">→</span></button>`).join('');

let updatePromptVersion='';
function renderUpdates(s){
  const u=s.update_info;
  if(!u)return;
  $('#app-version').textContent='v'+u.current;
  $('#update-version').textContent='Version installée : '+u.current;
  $('#update-status').textContent=u.message||'La recherche automatique s’effectue au lancement et toutes les six heures.';
  $('#update-auto').checked=u.enabled;
  const working=['checking','downloading','installing'].includes(u.status);
  $('#update-check').disabled=working;
  $('#update-install').hidden=u.status!=='available';
  $('#update-install').disabled=s.busy||working;
  $('#update-banner').hidden=!['available','downloading','installing'].includes(u.status);
  $('#update-banner-text').textContent=u.status==='available'?`La version ${u.latest} est disponible.`:u.message+(u.status==='downloading'?` ${u.progress} %`:'');
  $('#update-banner-install').hidden=u.status!=='available';
  $('#update-banner-install').disabled=s.busy||working;
  $('#update-notes').textContent=u.notes||'';
  $('#update-notes-details').hidden=!u.notes;
  if(u.status==='available'&&u.latest&&u.latest!==updatePromptVersion&&!s.busy&&!pendingConfirmation){
    updatePromptVersion=u.latest;
    confirmAction({title:'Une mise à jour est disponible',message:`Sauvegarde PC ${u.latest} est disponible (version actuelle : ${u.current}).\n\nL’installeur sera téléchargé depuis GitHub et vérifié. L’application se fermera pour vous permettre d’effectuer la mise à jour. Vos réglages et sauvegardes sont conservés.`,acceptLabel:'Mettre à jour'}).then(accepted=>{if(accepted)beginUpdate()});
  }
}
async function beginUpdate(){
  const result=await invoke('install_update');
  if(result&&!result.ok)notice(result.error,true);
  await refresh();
}
async function requestUpdate(){
  if(await confirmAction({title:'Mettre à jour Sauvegarde PC ?',message:'L’application téléchargera et vérifiera l’installeur, puis se fermera. Terminez l’assistant pour installer la nouvelle version. Vos sauvegardes et réglages seront conservés.',acceptLabel:'Mettre à jour'}))await beginUpdate();
}
$('#update-check').addEventListener('click',async()=>{updatePromptVersion='';await invoke('check_updates',true);await refresh()});
$('#update-install').addEventListener('click',requestUpdate);
$('#update-banner-install').addEventListener('click',requestUpdate);
$('#update-auto').addEventListener('change',e=>invoke('set_auto_check_updates',e.target.checked));

let softwarePrograms=[], softwarePlanSignature='';
async function loadSoftware(){
  const result=await invoke('list_software');
  if(result){softwarePrograms=result;renderSoftwareList();}
}
function renderSoftwareList(){
  const query=$('#software-search').value.toLocaleLowerCase();
  const items=softwarePrograms.filter(p=>p.name.toLocaleLowerCase().includes(query));
  $('#software-list').innerHTML=items.map(p=>`<div class="item"><span><strong>${esc(p.name)}</strong><br><small>${esc(p.version)} · ${esc(p.publisher)}</small></span><button class="secondary" data-software-analyze="${esc(p.id)}" ${state?.busy?'disabled':''}>Analyser</button></div>`).join('')||'<p class="hint">Aucun logiciel affiché. Chargez la liste ou modifiez votre recherche.</p>';
}
function renderSoftwareState(s){
  const p=s.software_plan;
  const signature=JSON.stringify(p);
  if(signature!==softwarePlanSignature){
    softwarePlanSignature=signature;
    $('#software-plan').innerHTML=p?`
      <h3>${esc(p.program.name)} · ${esc(p.program.version)}</h3><p>${esc(p.confidence)}</p>
      <h4>Installation sur un PC neuf</h4>
      <p class="installer-status">${esc(installerDescription(p.installer?.method))}</p>
      <p class="hint">${esc(p.installer?.label)}</p>
      ${p.engine===2?'<button class="secondary" data-software-installer="">Choisir un installeur MSI / EXE</button><p class="hint">Choisissez l’installeur officiel de ce logiciel, idéalement de la même version. Il sera copié dans la sauvegarde. Un EXE peut demander une connexion Internet ou des étapes dans son assistant.</p>':''}
      <h4>Fichiers et paramètres détectés</h4>
      ${p.files.map(f=>`<p class="hint software-path">${esc(f.source)}</p>`).join('')}
      ${p.registry.map(k=>`<p class="hint software-path">Registre : ${esc(typeof k==='string'?'HKCU\\'+k:k.hive+'\\'+k.key)}</p>`).join('')}
      ${!p.files.length&&!p.registry.length?'<p>Aucun paramètre détecté. Vous pouvez conserver l’installeur ; ajoutez vos données connues dans Fichiers personnels.</p>':''}
      <h4>Points à vérifier</h4><ul>${(p.notes||[]).map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
      ${(p.errors||[]).length?`<p class="hint">Analyse partielle :</p><ul>${p.errors.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}`:'';
  }
  $('#software-backup').disabled=s.busy||!p||(!p.files.length&&!p.registry.length&&!['msi','exe','winget'].includes(p.installer?.method));
  document.querySelectorAll('[data-software-analyze]').forEach(b=>b.disabled=s.busy);
  setHTML('#software-restores',(s.software_backups||[]).map(b=>`
    <div class="item software-snapshot"><div class="software-copy">
      <strong>${esc(b.name)}</strong> ${esc(b.version)}<br>
      <small>${esc(formatDate(b.created))} · ${b.count} fichiers · ${b.status==='complete'?'Copie terminée — couverture selon analyse':'Sauvegarde partielle'}</small>
      <p class="installer-status">${esc(b.schema===2?installerDescription(b.installer):'Ancienne sauvegarde : installez le logiciel avant de restaurer ses données.')}</p>
      ${b.website?`<small class="software-path">Site de l’éditeur : ${esc(b.website)}</small>`:''}
    </div><div class="software-actions">
      <button class="primary" data-software-restore="${esc(b.id)}" ${s.busy?'disabled':''}>${b.schema===2?'Installer et restaurer':'Restaurer les données'}</button>
      ${b.schema===2?`<button class="secondary" data-software-installer="${esc(b.id)}" ${s.busy?'disabled':''}>Ajouter / remplacer l’installeur</button>`:''}
    </div></div>`).join('')||'<p class="hint">Aucune sauvegarde de logiciel trouvée dans la source choisie.</p>');
  document.querySelectorAll('[data-software-installer]').forEach(b=>b.disabled=s.busy);
}
function installerDescription(method){
  return {msi:'Installation automatique depuis le MSI sauvegardé, puis restauration des paramètres.',
    winget:'Installation automatique via Winget (Internet requis), puis restauration des paramètres.',
    exe:'Installeur EXE sauvegardé : terminez son assistant, puis les paramètres seront restaurés.',
    manual:'Installeur manquant : ajoutez un MSI / EXE pour un PC neuf. Si le logiciel est déjà installé, ses paramètres peuvent être restaurés.'}[method]||'Installeur non identifié.';
}
async function softwareOperation(method,...args){
  const result=await invoke(method,...args);
  if(result&&!result.cancelled){notice(result.ok?'Opération démarrée. Consultez le journal d’activité.':result.error,!result.ok);await refresh();}
}
$('#software-search').addEventListener('input',renderSoftwareList);
document.addEventListener('click',async e=>{
  const analyze=e.target.closest('[data-software-analyze]');
  if(analyze){await softwareOperation('analyze_software',analyze.dataset.softwareAnalyze);return;}
  const installer=e.target.closest('[data-software-installer]');
  if(installer){await softwareOperation('choose_software_installer',installer.dataset.softwareInstaller||null);return;}
  const restore=e.target.closest('[data-software-restore]');
  if(restore){
    const snapshot=(state?.software_backups||[]).find(b=>b.id===restore.dataset.softwareRestore);
    const message=snapshot?.schema===2
      ?`Installer et restaurer ${snapshot.name} ?\n\n${installerDescription(snapshot.installer)}\nFermez le logiciel s’il est déjà installé. Utilisez une sauvegarde et un installeur de confiance. Les fichiers remplacés seront conservés dans la sauvegarde. Une installation échouée interrompt la restauration des paramètres.`
      :'Réinstallez puis fermez le logiciel avant de continuer. Les fichiers remplacés seront conservés dans la sauvegarde. Continuer ?';
    if(await confirmAction({title:snapshot?.schema===2?'Installer et restaurer le logiciel ?':'Restaurer les données ?',message,acceptLabel:snapshot?.schema===2?'Installer et restaurer':'Restaurer'}))await softwareOperation('restore_software',restore.dataset.softwareRestore);
  }
});
