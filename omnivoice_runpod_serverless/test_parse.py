import sys
import re
sys.path.append('.')
from app.presets import _parse_prompt_entry_line
line = "elder_female_02.wav : ရာသီဥတုပြောင်းတိုင်း ထိုင်ရ ထရ ခက်နေသူ‌တွေအတွက် Calcium Gold ကို ယခု ၄၀ ရာခိုင်နှုန်း အထူးလျှော့ဈေးနဲ့ ဝယ်လို့ရပါပြီ။, speed 1.36, numstep 38, guidance 4.2, volume 3.0"
res = _parse_prompt_entry_line(line)
print(res)
