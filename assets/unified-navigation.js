(function(){
'use strict';
const primary=[
 {label:'الأخبار',href:'./',key:'home'},
 {label:'الحوصلة الإخبارية',href:'./daily_cards.html',key:'daily'},
 {label:'مراكز البحوث',href:'./research_centers.html',key:'research'},
 {label:'التقديرات الاستراتيجية من المصادر الإخبارية',href:'./strategic_cards.html?type=news',key:'strategicnews'},
 {label:'التقديرات الاستراتيجية من مراكز البحوث',href:'./strategic_cards.html?type=research',key:'strategicresearch'},
 {label:'التقديرات الشهرية الشاملة',href:'./strategic_cards.html?type=monthly',key:'strategicmonthly'},
 {label:'الأرشيف',href:'./#archiveSection',key:'archive'}
];
const submenus={
 home:[['الأخبار الحالية','home'],['المقالات الاستراتيجية','analysis'],['أرشيف الأخبار','archive']],
 daily:[['الأخبار','./'],['مراكز البحوث','./research_centers.html'],['التقديرات من المصادر الإخبارية','./strategic_cards.html?type=news'],['التقديرات من مراكز البحوث','./strategic_cards.html?type=research'],['التقديرات الشهرية الشاملة','./strategic_cards.html?type=monthly']],
 strategicnews:[['التقديرات من المصادر الإخبارية','./strategic_cards.html?type=news'],['التقديرات من مراكز البحوث','./strategic_cards.html?type=research'],['التقديرات الشهرية الشاملة','./strategic_cards.html?type=monthly']],
 strategicresearch:[['التقديرات من المصادر الإخبارية','./strategic_cards.html?type=news'],['التقديرات من مراكز البحوث','./strategic_cards.html?type=research'],['التقديرات الشهرية الشاملة','./strategic_cards.html?type=monthly']],
 strategicmonthly:[['التقديرات من المصادر الإخبارية','./strategic_cards.html?type=news'],['التقديرات من مراكز البحوث','./strategic_cards.html?type=research'],['التقديرات الشهرية الشاملة','./strategic_cards.html?type=monthly']],
 research:[['أحدث المنشورات','./research_centers.html'],['التقديرات الاستراتيجية من مراكز البحوث','./strategic_cards.html?type=research']]
};
const path=location.pathname.split('/').pop()||'index.html';
const params=new URLSearchParams(location.search);
let pageKey=path==='daily_cards.html'?'daily':path==='strategic_cards.html'?({news:'strategicnews',research:'strategicresearch',monthly:'strategicmonthly'}[params.get('type')]||'strategicnews'):path==='research_centers.html'?'research':'home';
function el(tag,attrs,html){const n=document.createElement(tag);Object.keys(attrs||{}).forEach(k=>n.setAttribute(k,attrs[k]));if(html!==undefined)n.innerHTML=html;return n;}
function makeSubnav(key){
 const bar=el('nav',{class:'aso-subnav','aria-label':'التنقل داخل القسم'});
 const inner=el('div',{class:'aso-subnav-inner'});
 inner.appendChild(el('span',{class:'aso-subnav-label'},'داخل القسم'));
 (submenus[key]||submenus.home).forEach(([label,target])=>{
  if(key==='home'&&target!=='home'){
   const b=el('button',{type:'button'},label);
   b.addEventListener('click',()=>{const mapped=target==='analysis'?'strategicnews':target;const old=document.querySelector('.nav button[data-section="'+mapped+'"]');if(old)old.click();});
   inner.appendChild(b);
  }else{
   const a=el('a',{href:target==='home'?'./':target},label);
   if((key==='strategic'&&((params.get('type')||'')===({ 'البطاقات الإخبارية':'news','التقدير الشهري':'monthly','منشورات مراكز البحوث':'research'}[label]||'__none__')))||(key==='research'&&label==='أحدث المنشورات'))a.className='is-current';
   inner.appendChild(a);
  }
 });
 bar.appendChild(inner);return bar;
}
function addInnerShell(){
 document.body.classList.add('aso-inner-page');
 const old=document.querySelector('body>header:not(.aso-global-head)');
 const context=old?.querySelector('p')?.textContent?.trim()||'متابعة الأمن والدفاع في إفريقيا';
 const head=el('header',{class:'aso-global-head'});
 head.innerHTML='<div class="aso-topline">منصة متابعة الأمن والدفاع في إفريقيا</div><div class="aso-brandline"><a class="aso-brandlock" href="./"><span class="aso-brandmark" aria-hidden="true">◈</span><span><span class="aso-brandname">المرصد الإفريقي</span><br><span class="aso-branddesc">للأمن والدفاع</span></span></a><div class="aso-head-context">خبر • تحليل • متابعة ميدانية<br>قراءة في المشهد الأمني الإفريقي</div></div>';
 const nav=el('nav',{class:'aso-primary-nav','aria-label':'القائمة الرئيسية'});
 primary.forEach(item=>{const a=el('a',{href:item.href});a.textContent=item.label;if(item.key===pageKey)a.setAttribute('aria-current','page');nav.appendChild(a);});
 head.appendChild(nav);
 document.body.insertBefore(head,document.body.firstChild);
 head.after(makeSubnav(pageKey));
}
function addHomeSubnav(){
 const mast=document.querySelector('.masthead');
 if(!mast)return;
 mast.after(makeSubnav('home'));
 const buttons=document.querySelectorAll('.nav button');
 const update=()=>{
  const active=document.querySelector('.nav button.active');
  const key=active?.dataset.section||'home';
  const inner=document.querySelector('.aso-subnav-inner');
  if(!inner)return;
  inner.querySelectorAll('button').forEach(b=>b.classList.toggle('is-current',b.textContent.trim()===(active?.textContent||'').trim()));
 };
 buttons.forEach(b=>b.addEventListener('click',()=>setTimeout(update,0)));
 const observer=new MutationObserver(update);
 buttons.forEach(b=>observer.observe(b,{attributes:true,attributeFilter:['class']}));
 update();
}
if(pageKey==='home'){addHomeSubnav();window.addEventListener('load',()=>{const requested=new URLSearchParams(location.search).get('section');if(!requested||requested==='defense'||requested==='africa')return;const mapped=requested==='analysis'?'strategicnews':requested;const activate=()=>{const btn=document.querySelector('.nav button[data-section="'+mapped+'"]');if(btn){btn.click();return;}window.setTimeout(activate,180);};activate();});}else addInnerShell();
})();
