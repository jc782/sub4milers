from django.db import models

# Create your models here.

class AthleteSub4(models.Model):
    """
    Each object from this object is an athlete who has run sub 4.
    """
    name = models.CharField(max_length=1000, null=False, blank=False)
    firstTime = models.TimeField(null=False, blank=False)
    firstDate = models.DateField(null=True, blank=True) # date may be unknown
    firstIndoor = models.BinaryField(null=True, blank=True)
    pbTime = models.TimeField(null=True, blank=True)
    pbDate = models.DateField(null=True, blank=True) # date may be unknown
    pbIndoor = models.BinaryField(null=True, blank=True)
    countries = models.CharField(max_length = 100, blank=True, null=True)

    def bestTime(self):
          if self.pbTime:
                bestTime = self.pbTime
          else:
                bestTime = self.firstTime
          return bestTime
        
    def __str__(self):
            """
            The users name (first + last) is the returned string.
            """
            description = self.name + ' ran ' + self.firstTime.strftime("%M:%S.%f")
            return description
