import string
import hashlib
import uuid
import unicodedata
import re
from random import sample, choice, randint, shuffle

def gen_context_key():
    """
    Generate a globally-unique context key
    """
    return uuid.uuid4().hex
    
def get_uuid_salt(uuid):
    """
    Fetch the salt generated by the UUID of the VM
    
    This is a small salt (2 chars) that is only used to prohibit brute-forcing the password
    key in the database using rainbow tables. It is not very powerful, but it can
    be guessed both by the UUID and the pairing PIN. 
    
    The salt is calculated by scanning the 32 characters of the UUID looking for
    the first 4 digits. The salt is produced by those 4 digits like this:

     salt = D1+D2+D3+D4
    
    If there are no digits found, the default digits are: 1,2,3,4
    """
    
    # Return the UUID salt
    
    # Get the salt numbers
    salt = ['1','2','3','4']
    i=0
    numbers = '0123456789'
    for c in uuid:
        if (c in numbers):
            salt[i] = c
            i += 1
            if (i>=4):
                break
    
    # Return the salt number
    return ( int(salt[0]) + int(salt[1]) + int(salt[2]) + int(salt[3]) )

def gen_pin(length, encrypted, salt):
    """ 
    Generate a pairing key (PIN) 
    
    This pairing key contains additional hidden information
    that reduce the number of requests the user is required to perform. 
    Usually the pin is a 6-digit long random number with some special 
    rules on it's digits. Let's have this example of 6 digits:
    
      S  S  S  S  0  E
    
    S: Hidden salt for the Virtual Machine
       The salt is calculated like this str(int(S)+int(S)+int(S)+int(S))
    
    E: The last digit defines if the context is encrypted or not.
       1,3,5,7,9 (odd)  : Encrypted 
       0,2,4,6,8 (even) : Not encrypted
    
    0: The rest of the digits are just random numbers
    
    """
    
    # Use easy-to-remember characters, entrophy is not important here
    chars = '0123456789'
    chars_odd  = '13579'
    chars_even = '02468'
    
    # Build length-5 random part of the string
    rand_part = ''.join(choice(chars) for _ in range(length-5))
    
    # Create the last digit: 
    # odd -> encrypted
    # even -> not encrypted
    if encrypted:

        # Create 4 numbers that satisfy the following equation:
        # (n1+n2+n3+n4) = salt
        print "SALT = %i" % salt
        n1=0; n2=0; n3=0; n4=10; r=(salt/4)
        
        # Keep generating random numbers until all of
        # them are smaller than or equal to 9
        while (n4 > 9):
            n1 = randint(0,r)
            n2 = randint(0,r)
            n3 = randint(0,r)
            n4 = salt -(n1 + n2 + n3)

            print "NUMBERS = %i %i %i %i" % (n1,n2,n3,n4)
        
        # Randomize them a little more
        num = [str(n1),str(n2),str(n3),str(n4)]
        shuffle(num)
        
        # Make the string
        key = ''.join(num)
        
        # Discreetly mark the pin as encrypted
        key += rand_part+choice(chars_odd)
        
    else:
        
        # Append 4 random numbers if we are not encrypted
        key = ''.join(choice(chars) for _ in range(4)) + rand_part
        
        # Discreetly mark the pin as non-encrypted
        key += choice(chars_even)
    
    # Return the resulting string
    return key
    
def salt_context_key(uuid, key):
    """
    Salt the context key using SHA1 and our special salting mechanism
    
    The hash is produced like this: SHA1( salt + key + salt )
    (We use salt twice because it is very short - 2 chars)
    """
    
    # Get the string salt
    salt = "%02.i" % get_uuid_salt(uuid)
    
    # Return the salted result
    return hashlib.sha1(salt + key + salt).hexdigest()

def sanitize(s):
    """
    Sanitizes text.

    Takes str or unicode, returns str. Assumes that input str is utf-8. To see
    what characters are kept, look at the regexp.
    """

    if isinstance(s, str):
        s = s.decode('utf-8')
    elif not isinstance(s, unicode):
        raise TypeError('str or unicode expected (%s provided)' % \
            type(s).__name__)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore') # accents
    s = re.sub(r'[^A-Za-z0-9-\ _]+', '_', s) # invalid chars
    s = re.sub(r'(^[_\s]+|[_\s]+$)', '', s) # trailing/leading invalid/spaces
    return s

def sanitize_env(variable):
    
    # Sanitize stuff
    res = str(variable).replace("\\", "\\\\")
    for v in '!;&|<>()[]{}"\'':
        res = res.replace(v, "\\"+v)
        
    # Return result
    return res
