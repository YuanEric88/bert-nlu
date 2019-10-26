import os
import sys


def preprocessing(input_name, output_name):
    with open(output_name, "w") as f_out:
        with open(input_name, "r") as f_in:
            for line in f_in:
                sentence, tags = [], []
                pairs = line.split()
                for pair in pairs:
                    try:
                        word, tag = pair.rsplit("/", 1)
                        sentence.append(word)
                        tags.append(tag)
                    except:
                        print(pair)
                        pass
                sentence = " ".join(sentence)
                tags = " ".join(tags)
                f_out.write(sentence + "\t" + tags + "\n")


if __name__ == "__main__":
    input_name = sys.argv[1]
    output_name = sys.argv[2]

    preprocessing(input_name, output_name)