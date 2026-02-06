import os
import asyncio
import aiohttp
from datetime import datetime
import json
import hashlib
from typing import Dict, List
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SheinRailwayBot:
    def __init__(self):
        # Get credentials from Railway environment variables
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not self.bot_token or not self.chat_id:
            logger.error("❌ Missing Telegram credentials!")
            raise ValueError("Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway variables")
        
        self.bot = Bot(token=self.bot_token)
        
        # SHEIN Verse Cookies (Railway environment se)
        self.cookie_string = os.getenv("SHEIN_COOKIES", "")
        
        if not self.cookie_string:
            logger.warning("⚠️ No SHEIN cookies provided")
        
        self.cookies = self.parse_cookies(self.cookie_string)
        
        # Headers
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-IN,en;q=0.9',
        }
        
        # SHEIN Verse API URLs
        self.urls = {
            "men_new": "https://www.shein.in/api/goodsList/get?cat_id=22542&page=1&page_size=50&sort=7",
            "men_all": "https://www.shein.in/api/goodsList/get?cat_id=22542&page=1&page_size=100",
            "women_all": "https://www.shein.in/api/goodsList/get?cat_id=22543&page=1&page_size=100",
        }
        
        # Memory cache (Railway pe Redis optional hai)
        self.memory_cache = {}
        self.tracked_products = {}
        
        # Scheduler
        self.scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        
        # Stats
        self.stats = {
            "men_count": 0,
            "women_count": 0,
            "alerts_sent": 0,
            "last_alert": None,
            "start_time": datetime.now()
        }
    
    def parse_cookies(self, cookie_str: str) -> Dict:
        """Parse cookie string"""
        cookies = {}
        if cookie_str:
            for cookie in cookie_str.strip().split(';'):
                if '=' in cookie:
                    key, value = cookie.strip().split('=', 1)
                    cookies[key] = value
        return cookies
    
    async def fetch_shein_data(self, url: str) -> Dict:
        """Fetch data from SHEIN"""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=self.headers
            ) as session:
                
                # Add cookies if available
                for key, value in self.cookies.items():
                    session.cookie_jar.update_cookies({key: value})
                
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"HTTP {response.status} for {url}")
                        return {}
                        
        except Exception as e:
            logger.error(f"Error: {e}")
            return {}
    
    async def get_products(self, url_key: str) -> List[Dict]:
        """Get products from API"""
        data = await self.fetch_shein_data(self.urls[url_key])
        
        if not data or 'info' not in data or 'goods' not in data['info']:
            return []
        
        products = []
        for item in data['info']['goods'][:30]:  # First 30 only for speed
            try:
                product_id = str(item.get('goods_id', ''))
                
                product = {
                    'id': product_id,
                    'name': item.get('goods_name', 'Unknown')[:60],
                    'price': item.get('salePrice', {}).get('amount', 'N/A'),
                    'image': f"https:{item.get('goods_img', '')}" if item.get('goods_img') else None,
                    'link': f"https://www.shein.in{item.get('goods_url_path', '')}",
                    'category': 'men' if '22542' in self.urls[url_key] else 'women',
                    'in_stock': True,
                    'timestamp': datetime.now().isoformat()
                }
                
                products.append(product)
                
            except Exception as e:
                continue
        
        return products
    
    async def check_stock_30sec(self):
        """Check stock every 30 seconds"""
        try:
            men_products = await self.get_products("men_new")
            
            for product in men_products:
                # Check if new product
                if product['id'] not in self.tracked_products:
                    await self.send_alert(product)
                    self.tracked_products[product['id']] = product
            
            # Update stats
            self.stats["men_count"] = len(men_products)
            self.stats["women_count"] = len(await self.get_products("women_all"))
            
            logger.info(f"✅ Checked: {len(men_products)} men's products")
            
        except Exception as e:
            logger.error(f"Check error: {e}")
    
    async def send_alert(self, product: Dict):
        """Send alert to Telegram"""
        try:
            alert_msg = f"""
🆕 *NEW SHEIN VERSE STOCK*
━━━━━━━━━━━━━━━━━━━━━━
👕 *{product['name']}*
💰 *Price*: ₹{product['price']}
🔗 [BUY NOW]({product['link']})
⏰ *Time*: {datetime.now().strftime('%I:%M:%S %p')}
━━━━━━━━━━━━━━━━━━━━━━
⚡ *Fast Buy Required!*
            """
            
            # Try to send image
            if product.get('image'):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(product['image'], timeout=5) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                await self.bot.send_photo(
                                    chat_id=self.chat_id,
                                    photo=image_data,
                                    caption=alert_msg,
                                    parse_mode=ParseMode.MARKDOWN
                                )
                                self.stats["alerts_sent"] += 1
                                self.stats["last_alert"] = datetime.now().strftime("%H:%M:%S")
                                return
                except:
                    pass
            
            # Text only fallback
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=alert_msg,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False
            )
            
            self.stats["alerts_sent"] += 1
            self.stats["last_alert"] = datetime.now().strftime("%H:%M:%S")
            
        except Exception as e:
            logger.error(f"Alert failed: {e}")
    
    async def send_summary_2hr(self):
        """Send summary every 2 hours"""
        try:
            uptime = datetime.now() - self.stats["start_time"]
            hours = uptime.seconds // 3600
            
            summary = f"""
📊 *SHEIN BOT SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━
⏰ Uptime: {hours} hours
👕 Men's: {self.stats['men_count']}
👚 Women's: {self.stats['women_count']}
🔔 Alerts: {self.stats['alerts_sent']}
⏱️ Last: {self.stats['last_alert'] or 'None'}
━━━━━━━━━━━━━━━━━━━━━━
✅ Bot is running on Railway
            """
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=summary,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Summary error: {e}")
    
    async def send_start_message(self):
        """Send startup message"""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="🚀 *SHEIN VERSE BOT STARTED*\n\n✅ Now monitoring stock every 30 seconds!\n📱 Alerts will come with images & buy links.",
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info("✅ Startup message sent")
        except Exception as e:
            logger.error(f"Start message failed: {e}")
    
    def setup_schedulers(self):
        """Setup all schedulers"""
        # Every 30 seconds
        self.scheduler.add_job(
            self.check_stock_30sec,
            IntervalTrigger(seconds=30),
            id='30sec_check'
        )
        
        # Every 2 hours
        self.scheduler.add_job(
            self.send_summary_2hr,
            IntervalTrigger(hours=2),
            id='2hr_summary'
        )
    
    async def run(self):
        """Main run function"""
        try:
            # Send startup message
            await self.send_start_message()
            
            # Setup schedulers
            self.setup_schedulers()
            
            # Start scheduler
            self.scheduler.start()
            logger.info("✅ Bot started on Railway")
            
            # Initial check
            await self.check_stock_30sec()
            
            # Keep running
            while True:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Bot stopped")
        except Exception as e:
            logger.error(f"Fatal error: {e}")

# Run the bot
if __name__ == "__main__":
    print("""
    ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃   SHEIN BOT ON RAILWAY    ┃
    ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
    """)
    
    bot = SheinRailwayBot()
    
    # Create event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
