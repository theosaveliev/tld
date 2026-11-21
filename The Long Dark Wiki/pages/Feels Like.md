---
title: "Feels Like"
wiki_namespace: 0
categories: ["Gameplay"]
source: "https://thelongdark.fandom.com/wiki/Feels_Like"
---

# Feels Like

<div>

**Contents**

- [Basic](#Basic)
- [Calculating 'Feels like'](#Calculating_%27Feels_like%27)
- [Air Temp](#Air_Temp)
- [Wind Chill](#Wind_Chill)
- [Clothing](#Clothing)
- [Heat Sources](#Heat_Sources)
- [Sleeping Areas](#Sleeping_Areas)
- [Trivia](#Trivia)

**Feels Like** is the catch-all result of all the variables and calculations affecting <a href="./Warmth.md" class="wikilink" title="Warmth">Warmth</a>. Having one number to manage simplifies the maintenance of warmth. Understanding how the number is generated allows deeper understanding of what can be done to manage it.

## Basic

The character gains Warmth as long as 'feels like' remains above 0°C (32°F). The character loses Warmth whenever 'feels like' is below 0°C (32°F). When no warmth remains, the character loses condition until 'feels like' is brought back above 0°C (32°F). See: <a href="./Warmth.md" class="wikilink" title="Warmth">Warmth</a>.

## Calculating 'Feels like'

'Feels like' temperature begins with a sum of all the temperatures on the Quick Stats menu in the <a href="./Survival%20Menu.md" class="wikilink" title="Survival Menu">Survival Menu</a>: Air Temp, Wind Chill, and the two Clothing Bonuses. It can also include a warming bonus when applicable.

### Air Temp

Air temp as displayed on the Survival Page is the result of calculations determining the temperature of the air around the character. Interior locations have stable temperatures. Exterior locations are more variable being weather dependent. Additionally, <a href="./Fire.md" class="wikilink" title="fire">fires</a> directly increase local air temperatures with their heat output. However, the maximum air temperature is 40°C, though maximum fire output is 80°C for wood fuels and 200°C for coal, on real Life those temperatures are very dangerous to humans and animals.

### Wind Chill

Calculated directly from wind speed and air temp (See: <a href="./Weather.md" class="wikilink" title="Weather">Weather</a>), this is an additional reduction made to 'Feels like' temperatures (never positive) when wind is blowing. It is possible to avoid wind, and therefore wind chill, by shielding the character from the direction of the wind with terrain or structures (see: <a href="./Survival%20Overlay.md" class="wikilink" title="Wind shield">Wind shield</a>).

### Clothing

<a href="./Clothing.md" class="wikilink" title="Clothing">Clothing</a> items have specific warming attributes, one for air temp protection and one for wind chill protection. Wearing that item of clothing grants that protection. The sum of all clothing worn is visible as the clothing bonus and clothing air temp bonus on the Survival Page. These bonuses are added directly when calculating the 'Feels like' temperature.

- Note: Clothing wind chill bonus will only cancel out actual wind chill. If the current wind chill is -4°C and clothing has a wind bonus of +6°C, only 4°C of clothing wind bonus will actually be used. If there is no wind chill, wind chill bonus will have no effect until a negative wind chill is encountered.
- Only the outer layer clothing affects the wind chill protection. Layered clothing below the outer one do not affect the wind chill protection even if they have a higher stat than the outer layer.

### Heat Sources

<a href="./Fire.md" class="wikilink" title="Fire">Fires</a> give off heat. Observing a fire will display its current heat output. That heat is added to directly to the natural air temperatures around the fire. Moving away from a fire will see this effect gradually fade with distance. Adding more fuel to a fire will raise its temperature output (and thus the surrounding air temp) up to a cap of 80°C. Fires lit in the <a href="./Furnace.md" class="wikilink" title="furnace">furnace</a> can be boosted up to an incredible 200°C using coal. Regardless, air temp will stop increasing when it reaches 40°C. The heat output from a fire can only be reduced by removing torches from the same fire. Otherwise, the heat will remain for the life of the fire.

<a href="./Torch.md" class="wikilink" title="Torch">Torches</a>, <a href="./Flare.md" class="wikilink" title="flare">flares</a>, <a href="./Matches%20%28disambiguation%29.md" class="wikilink" title="matches">matches</a>, and <a href="./Storm%20Lantern.md" class="wikilink" title="storm lanterns">storm lanterns</a> all give off heat when lit. Behaving like fire, these portable heat sources provide heat independently. Thus, holding a torch while standing next to a fire is warmer than either one alone. Portable heat sources also grant this benefit while on the ground, but their radius of effect is much smaller than fire's. In fact, when lying on the ground, their effect can only be felt by standing directly over them and crouching down.

### Sleeping Areas

When <a href="./Sleep.md" class="wikilink" title="Sleep">Sleeping</a> the <a href="./Bed.md" class="wikilink" title="bed">bed</a> chosen grants insulation to the occupant. How much is displayed on the Sleep Interface as Warmth Bonus and varies dependent on quality. This can be as low as +0.1° for a worn out <a href="./Bedroll.md" class="wikilink" title="bedroll">bedroll</a> to as high as +12.0°C for a pristine <a href="./Bear%20Skin%20Bedroll.md" class="wikilink" title="Bear skin bedroll">Bear skin bedroll</a>. During the time of sleep, Feel Like temperature will gain this bonus. This bonus will never be seen on the Survival Page however because it only occurs while sleeping and disappears immediately upon waking up.

## Trivia

- Expressed in degrees of temperature, it is always shown rounded to nearest integer though it is obvious that decimal calculations are carried. So even though 'feels like' shows exactly freezing, you are likely either gaining or losing heat based on the carried fraction.

**Lua execution error in Module:Navbox function navbox**

</div>
