const mineflayer = require('mineflayer');

const [host, port = '25565', username, version = '1.21.1'] = process.argv.slice(2);
const GEMINI_KEY = process.env.GEMINI_KEY;
const GIFTS_ENABLED = process.env.WELCOME_GIFTS_ENABLED !== 'false';
const MIN_GIFT_DELAY_MS = Number(process.env.GIFT_DELAY_MIN_HOURS || 1) * 60 * 60 * 1000;
const MAX_GIFT_DELAY_MS = Number(process.env.GIFT_DELAY_MAX_HOURS || 2) * 60 * 60 * 1000;
if (!host || !username) {
  console.error('Usage: node afk_client.js host port username [version]');
  process.exit(2);
}
if (!GEMINI_KEY) {
  console.error('GEMINI_KEY is required for Minecraft AI chat.');
  process.exit(2);
}

let bot;
let afkInterval;
let reconnectTimer;
let stopping = false;
let answering = false;
let lastAnswerAt = 0;
const giftTimers = new Map();
const rewardedPlayers = new Set();
const COOLDOWN_MS = 8000;

function cleanText(text) {
  return String(text).replace(/\s+/g, ' ').trim().slice(0, 300);
}

function safePlayerName(name) {
  return /^[A-Za-z0-9_]{1,16}$/.test(name) ? name : null;
}

function scheduleWelcomeGift(player) {
  if (!GIFTS_ENABLED || player === username || rewardedPlayers.has(player) || giftTimers.has(player)) return;
  const min = Math.max(0, MIN_GIFT_DELAY_MS);
  const max = Math.max(min, MAX_GIFT_DELAY_MS);
  const delay = Math.floor(min + Math.random() * (max - min + 1));
  console.log(`Gift scheduled for ${player} in ${Math.round(delay / 60000)} minutes`);
  const timer = setTimeout(() => giveWelcomeGift(player), delay);
  giftTimers.set(player, timer);
}

function giveWelcomeGift(player) {
  giftTimers.delete(player);
  const target = safePlayerName(player);
  if (!target || !bot || !bot.players[target]) return;
  const gifts = [
    ['diamond', 32], ['iron_ingot', 45], ['emerald', 60],
    ['coal', 80], ['gold_ingot', 100]
  ];
  for (const [item, count] of gifts) bot.chat(`/give ${target} minecraft:${item} ${count}`);
  rewardedPlayers.add(player);
  bot.chat(`🎁 ${target} حصل على هدية الدخول المجانية!`);
  console.log(`Welcome gift sent to ${target}`);
}

async function askGemini(player, question) {
  const endpoint = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${encodeURIComponent(GEMINI_KEY)}`;
  const prompt = `أنت لاعب Minecraft اسمه ${username}. جاوب اللاعب ${player} باختصار وبأسلوب ودود. لا تدّعي أنك إنسان ولا تطلب كلمات السر أو بيانات شخصية. السؤال: ${question}`;
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] })
  });
  if (!response.ok) throw new Error(`Gemini HTTP ${response.status}`);
  const data = await response.json();
  return data?.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || 'ما فهمتش السؤال.';
}

function connect() {
  bot = mineflayer.createBot({ host, port: Number(port), username, version, auth: 'offline' });
  bot.once('spawn', () => {
    console.log(`AI Minecraft bot connected to ${host}:${port}`);
    afkInterval = setInterval(() => {
      if (!bot || !bot.entity) return;
      bot.setControlState('left', true);
      setTimeout(() => bot?.setControlState('left', false), 400);
      bot.swingArm('right');
    }, 15000);
  });
  bot.on('playerJoined', player => scheduleWelcomeGift(player.username));
  bot.on('chat', async (player, message) => {
    if (player === username) return;
    const text = cleanText(message);
    const lower = text.toLowerCase();
    const addressed = lower.startsWith('!ai ') || lower.startsWith(`${username.toLowerCase()} `);
    if (!addressed || answering || Date.now() - lastAnswerAt < COOLDOWN_MS) return;
    const question = text.replace(/^!ai\s+/i, '').replace(new RegExp(`^${username}\\s+`, 'i'), '').trim();
    if (!question) return;
    answering = true;
    lastAnswerAt = Date.now();
    try { bot.chat(`${player}: ${cleanText(await askGemini(player, question))}`); }
    catch (error) { console.error('Minecraft AI error:', error.message); bot.chat(`${player}: عذراً، الذكاء الاصطناعي غير متاح دابا.`); }
    finally { answering = false; }
  });
  bot.on('kicked', reason => console.error('Kicked:', reason));
  bot.on('error', error => console.error('Minecraft error:', error.message));
  bot.once('end', () => {
    if (afkInterval) clearInterval(afkInterval);
    for (const timer of giftTimers.values()) clearTimeout(timer);
    giftTimers.clear();
    console.log('Minecraft AI bot disconnected');
    if (!stopping) {
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 10000);
      console.log('Reconnecting in 10 seconds...');
    }
  });
}

function shutdown() {
  stopping = true;
  clearTimeout(reconnectTimer);
  if (afkInterval) clearInterval(afkInterval);
  for (const timer of giftTimers.values()) clearTimeout(timer);
  if (bot) bot.quit('AI bot stopped');
  setTimeout(() => process.exit(0), 500);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
connect();
