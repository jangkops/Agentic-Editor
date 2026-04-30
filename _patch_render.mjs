import fs from 'fs';
const p = '/Users/jcg/agentic-editor/src/main.js';
let src = fs.readFileSync(p, 'utf8');

// 1) renderMessages 헤더 교체
const oldHeader = `// ===== Render Messages =====
function renderMessages(){
  const c=document.getElementById('chat-messages');c.innerHTML='';`;

const newHeader = fs.readFileSync('/Users/jcg/agentic-editor/src/main.js.patch_tmp', 'utf8');

if(!src.includes(oldHeader)){
  console.error('HEADER NOT FOUND');
  process.exit(1);
}
src = src.replace(oldHeader, newHeader);

// 2) renderMessages 끝(c.scrollTop=c.scrollHeight; }) 뒤에 캐시 업데이트 추가
const oldTail = `  c.scrollTop=c.scrollHeight;
}

// SVG 아이콘`;
const newTail = `  // 렌더 캐시 업데이트 (스트리밍 fast-path 판단용)
  const visible = state.messages.filter(m=>!m.hiddenInChat);
  _renderCache.count = visible.length;
  const lastV = visible[visible.length-1];
  _renderCache.lastLen = lastV && lastV.content ? lastV.content.length : 0;
  _renderCache.lastKind = lastV ? lastV.role : '';
  // 스크롤: 사용자가 위로 스크롤한 상태면 강제로 내리지 않음
  const nearBottom = (c.scrollHeight - c.scrollTop - c.clientHeight) < 80;
  if(nearBottom) c.scrollTop = c.scrollHeight;
}

// SVG 아이콘`;

if(!src.includes(oldTail)){
  console.error('TAIL NOT FOUND');
  process.exit(1);
}
src = src.replace(oldTail, newTail);

fs.writeFileSync(p, src);
console.log('PATCH OK');
