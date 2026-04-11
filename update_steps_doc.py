import os
import re

base_dir = "/home/suriya/Documents/ARU_AIML/PROJECT_app_ml/documentation"

# Ordered list of replacements (order matters to avoid overriding)
replacements = [
    # Replace exact wording
    (r'Step 1a\b', 'Step 1'),
    (r'Step 2a\b', 'Step 2'),
    (r'Step 1b\b', 'Step 3'),
    (r'Step 2b\b', 'Step 4'),
    (r'Step 2\.5\b', 'Step 5'),
    (r'Step 3\b', 'Step 6'),
    (r'Step 4\b', 'Step 7'),
    (r'Step 5\b', 'Step 8'),
    (r'Step 6\b', 'Step 9'),
    (r'Step 7\b', 'Step 10'),
    
    # Capitalized / Title representations that might exist
    (r'1a\b', '1'),
    (r'2a\b', '2'),
    (r'1b\b', '3'),
    (r'2b\b', '4'),
]

for root, dirs, files in os.walk(base_dir):
    for f in files:
        if not f.endswith(".md"):
            continue
        path = os.path.join(root, f)
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
            
        new_content = content
        
        # Safe replacement for specific step numbers
        # We manually replace the "Step X" strings
        new_content = new_content.replace('Step 1a', 'Step 1')
        new_content = new_content.replace('Step 2a', 'Step 2')
        new_content = new_content.replace('Step 1b', 'Step 3')
        new_content = new_content.replace('Step 2b', 'Step 4')
        new_content = new_content.replace('Step 2.5', 'Step 5')
        
        # For the simpler numbers, we have to be careful not to replace text that meant something else.
        # So we only replace occurrences that have "Step N"
        new_content = re.sub(r'Step 7\b', 'Step 10', new_content)
        new_content = re.sub(r'Step 6\b', 'Step 9', new_content)
        new_content = re.sub(r'Step 5\b', 'Step 8', new_content)
        new_content = re.sub(r'Step 4\b', 'Step 7', new_content)
        new_content = re.sub(r'Step 3\b', 'Step 6', new_content)

        if new_content != content:
            with open(path, "w", encoding="utf-8") as file:
                file.write(new_content)
            print(f"Updated step numbers in {f}")

