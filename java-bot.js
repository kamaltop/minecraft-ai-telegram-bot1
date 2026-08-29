import 'dotenv/config';
import mineflayer from 'mineflayer';

const host = process.env.MC_HOST || 'localhost';
const port = Number(process.env.MC_PORT || 25565);
const username = process.env.MC_USERNAME || 'AFK_Bot';
const version = process.env.MC_VERSION || false;
const intervalMs = Number(process.env.AFK_INTERVAL_MS || 45000);
let reconnectTimer;

function start() {
  console.log(`[Java] Connecting to ${host}:${port} as ${username}...`);
  const bot = mineflayer.createBot({
    host,
    port,
    username,
    version: version || undefined,
    auth: process.env.MC_AUTH || 'offline'
  });

  bot.once('spawn', () => {
    console.log('[Java] Connected. AFK routine started.');
    const routine = setInterval(() => {
      if (!bot.entity) return;
      bot.look(bot.entity.yaw + 0.8, bot.entity.pitch, true).catch(() => {});
      bot.setControlState('jump', true);
      setTimeout(() => bot.setControlState('jump', false), 700);
    }, intervalMs);
    bot.once('end', () => clearInterval(routine));
  });

  bot.on('kicked', (reason) => console.warn('[Java] Kicked:', reason));
  bot.on('error', (error) => console.error('[Java] Error:', error.message));
  bot.on('end', () => {
    console.log('[Java] Disconnected. Reconnecting in 10 seconds...');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(start, 10000);
  });
}

start();
