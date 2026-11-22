# Big Cat Killer

**Categories:** Feats · **Source:** [Fandom](https://thelongdark.fandom.com/wiki/Big_Cat_Killer)

**Big Cat Killer**

[Master Hunter icon](../assets/Master_Hunter_icon.png)

**Description:** You've successfully killed 3 Cougars in a single game.

**Benefit:** Wildlife are less aware of your presence.

**Effect:** Halves detection range from all animals (sight, hearing, and smell)

**DLC required:** [Tales from the Far Territory](./Tales%20from%20the%20Far%20Territory.md)

**Big Cat Killer** is a [feat](./Feats.md) in The Long Dark. It was first made available in version 2.28 when the Cougar (now commonly called "[Cougar 1.0](./Cougar.md#Legacy_Information%3A_Cougar_1.0)") was released. The feat is considered "legacy" because it can no longer be acquired in the current versions of the game, but can still be earned using an older version.

- Big Cat Killer halves detection range, sight, hearing, and smell from all animals, allowing the player to get **much** closer to all [Wildlife](./Wildlife.md) before detection.
- Unlike all other Feats, progression for Big Cat Killer required killing all three [Cougars](./Cougar.md#Legacy_Information%3A_Cougar_1.0) in the same save file. It also didn't show the percentage progress in the menu.
- The only legitimate way to obtain Big Cat Killer is to clear out your survival profiles, play version 2.28 or 2.29 (versions with the old Cougar) and kill three Cougars in the same save file. This process is equivalent to having obtained the Feat in the old version during the short time it was available.
- Big Cat Killer has been deemed a legacy Feat due to it "no longer seeming appropriate" for the reworked Cougar. It's planned to be replaced with a different "Cougar-related" Feat.

## Instructions

The following instructions work for Steam only and require manual manipulation of saved game files, settings, and profile files. Great care should be taken to maintain full backups in case of a problem.

Backup existing files

1. Using Explorer, navigate to %LOCALAPPDATA%\\Hinterland (or C:\\Users\\&lt;name&gt;\\AppData\\Local\\Hinterland\\) folder. Copy the Hinterland folder and all contents to a backup location.

Install game version 2.29

1. In your default browser, type [steam://nav/console](steam://nav/console) into the address bar. This will open a Console tab in your Steam client.
2. Enter the following commands into the Console one at a time, waiting for each to complete before moving on. This step will download the base game and TFTFT DLC for version 2.29 (25 June 2024) of The Long Dark to your local system. This will not interfere with your current installation.
  - download\_depot 305620 305621 2547860543836978015
  - download\_depot 305620 2091331 4364909505189144708
3. Go to the "app\_305620" folder mentioned in the "Depot download complete" messages. Move the "tld\_dlc" folder from within the "depot\_2091331" into the "depot\_305621" folder.
4. Launch "tld.exe" from within the "depot\_305621" folder. Create a new save - you may want to name it something like "v2.29 Big Cat Killer". It is critical that you not open this save file with the current version of the game, nor use v2.29 to open other existing saves.
5. Complete the requirements for "Big Cat Killer". Do a final save of the game after getting the Feat. Start making a new save in the v2.29 game just to verify that Big Cat Killer is presented as an option.
6. Exit the v2.29 game completely.
7. Using Explorer, navigate to %LOCALAPPDATA%\\Hinterland (or C:\\Users\\&lt;name&gt;\\AppData\\Local\\Hinterland\\) folder. Copy the Hinterland folder and all contents to a backup location. Name it something like "Hinterland v2.29".

Finishing up

1. Using Explorer, navigate to %LOCALAPPDATA%\\Hinterland\\TheLongDark\\Survival (or C:\\Users\\&lt;name&gt;\\AppData\\Local\\Hinterland\\TheLongDark\\Survival).
2. There will be a file "profile\_survival" and several numbered files like "profile\_survival.######". Make sure Explorer is showing the Details view and find the "profile\_survival.######" file that has the most recent date and time. Delete the other "profile\_survival.######" files and delete "profile\_survival".
3. Copy the "profile\_survival.######" file that you kept, and rename the copy "profile\_survival".
4. Launch the current version of The Long Dark. "Big Cat Killer" should now be visible in the Feats list. Ensure all your prior Feats and Badges appear correct.
