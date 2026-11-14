import random

def practice_features(band_name):
    print(f"Welcome to {band_name} Practice Features!")

    # 1. Song selection
    song_list = ["Song A", "Song B", "Song C"]
    selected_song = random.choice(song_list)
    print(f"Today's song is: {selected_song}")

    # 2. Rhythm and timing exercises
    print("Let's focus on rhythm and timing:")
    for i in range(5):
        print(f"Play the {i+1} beat")

    # 3. Scales and arpeggios
    scale_type = random.choice(["major", "minor"])
    print(f"Now, let's practice scales ({scale_type}):")
    for i in range(5):
        print(f"Play the {i+1} note of the scale")

    # 4. Improvisation exercises
    print("Time to improvise!")
    for i in range(3):
        print(f"Create a solo over the chord progression ({i+1}/2)")

    # 5. Performance and recording practice
    print("Let's record our performance:")
    for i in range(2):
        print(f"Record take {i+1}")

print("Practice features complete! See you next time, band!")