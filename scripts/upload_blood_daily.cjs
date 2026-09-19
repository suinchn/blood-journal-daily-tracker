#!/usr/bin/env node
'use strict';
// upload_blood_daily.cjs — 将血液日报上传到 IMA 知识库「血液笔记」→「血液日报」文件夹
// 流程：import_doc 创建笔记 → add_knowledge 关联到知识库（media_type=11 笔记类型）
// 凭证：环境变量 IMA_OPENAPI_CLIENTID/IMA_OPENAPI_APIKEY 优先，否则读 ~/.config/ima/{client_id,api_key}
// Usage: node upload_blood_daily.cjs <YYYYMMDD> [path/to/report.md]

const https = require('https');
const { readFileSync, existsSync } = require('fs');
const path = require('path');
const os = require('os');

const KB_ID = 'OrliMk9lo0HEPm-3k4qiDl803zUWG_K1HOcQiNK9lo4=';   // 血液笔记知识库
const FOLDER_ID = 'folder_7490548709474198';                    // 血液日报文件夹

function loadCredentials() {
  if (process.env.IMA_OPENAPI_CLIENTID && process.env.IMA_OPENAPI_APIKEY) {
    return { clientId: process.env.IMA_OPENAPI_CLIENTID, apiKey: process.env.IMA_OPENAPI_APIKEY };
  }
  const clientPath = path.join(os.homedir(), '.config', 'ima', 'client_id');
  const keyPath = path.join(os.homedir(), '.config', 'ima', 'api_key');
  if (existsSync(clientPath) && existsSync(keyPath)) {
    return { clientId: readFileSync(clientPath, 'utf-8').trim(), apiKey: readFileSync(keyPath, 'utf-8').trim() };
  }
  console.error('ERROR: No IMA credentials found (~/.config/ima/client_id + api_key)');
  process.exit(1);
}

const { clientId, apiKey } = loadCredentials();

function ima_api(apiPath, body) {
  return new Promise((resolve, reject) => {
    const jsonBody = JSON.stringify(body);
    const options = {
      hostname: 'ima.qq.com',
      path: '/' + apiPath,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        'ima-openapi-clientid': clientId,
        'ima-openapi-apikey': apiKey,
        'Content-Length': Buffer.byteLength(jsonBody)
      }
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse response: ${data.substring(0, 200)}`));
        }
      });
    });
    req.on('error', (e) => reject(e));
    req.write(jsonBody);
    req.end();
  });
}

async function main() {
  const date = process.argv[2]; // e.g. "20260809"
  if (!date) {
    console.error('Usage: node upload_blood_daily.cjs <YYYYMMDD> [path/to/report.md]');
    process.exit(1);
  }

  // 默认报告路径：skill 的 data 目录
  const DEFAULT_REPORT = path.join(__dirname, '..', 'data', `daily_report_${date}_formatted.md`);
  const reportPath = process.argv[3] || DEFAULT_REPORT;

  if (!existsSync(reportPath)) {
    console.error(`ERROR: Report not found at ${reportPath}`);
    process.exit(1);
  }

  const content = readFileSync(reportPath, 'utf-8');
  const dateFormatted = `${date.substring(0,4)}-${date.substring(4,6)}-${date.substring(6,8)}`;
  const title = `🩸 血液朝夕录 - ${dateFormatted}`;

  console.log(`=== IMA Upload: 血液笔记 → 血液日报 for ${dateFormatted} ===`);

  // Step 1: 创建笔记 import_doc（先不带 folder_id，避免 310001）
  console.log('Step 1: Creating note via import_doc...');
  let noteResult = await ima_api('openapi/note/v1/import_doc', {
    content_format: 1, // Markdown
    content: content,
    title: title
  });

  if (noteResult.code !== 0) {
    console.error(`Failed to create note: code=${noteResult.code}, msg=${noteResult.msg}`);
    noteResult = await ima_api('openapi/note/v1/import_doc', {
      content_format: 1,
      content: content,
      folder_id: ''
    });
    if (noteResult.code !== 0) {
      console.error(`Retry failed: code=${noteResult.code}, msg=${noteResult.msg}`);
      process.exit(1);
    }
  }

  const noteId = noteResult.data?.note_id || noteResult.data?.content_id || noteResult.note_id;
  if (!noteId) {
    console.error('No note_id returned:', JSON.stringify(noteResult).substring(0, 300));
    process.exit(1);
  }
  console.log(`Note created: note_id=${noteId}`);

  // Step 2: 关联到知识库 add_knowledge（media_type=11 笔记类型，folder_id=血液日报）
  console.log('Step 2: Adding to KB 血液笔记 → 血液日报...');
  let kbResult = await ima_api('openapi/wiki/v1/add_knowledge', {
    media_type: 11,
    note_info: { content_id: noteId },
    title: title,
    knowledge_base_id: KB_ID,
    folder_id: FOLDER_ID
  });

  if (kbResult.code !== 0) {
    console.error(`Failed to add to KB: code=${kbResult.code}, msg=${kbResult.msg}`);
    console.log('Retrying without folder_id...');
    kbResult = await ima_api('openapi/wiki/v1/add_knowledge', {
      media_type: 11,
      note_info: { content_id: noteId },
      title: title,
      knowledge_base_id: KB_ID
    });
    if (kbResult.code !== 0) {
      console.error(`Retry failed: code=${kbResult.code}, msg=${kbResult.msg}`);
      process.exit(1);
    }
    console.log('Added to KB without folder_id (manual move may be needed)');
  } else {
    console.log('Added to KB with folder_id ✓');
  }

  console.log(`\n✅ Upload complete: note_id=${noteId}, media_id=${kbResult.data?.media_id || 'N/A'}`);
  console.log(`   KB: 血液笔记 (${KB_ID}) / 文件夹: 血液日报 (${FOLDER_ID})`);
}

main().catch(e => {
  console.error(`Fatal error: ${e.message}`);
  process.exit(1);
});
