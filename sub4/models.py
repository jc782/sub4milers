from django.db import models

# Create your models here.

class AthleteSub4(models.Model):
    """
    Each object from this object is an athlete who has run sub 4.
    """
    name = models.CharField(max_length=1000, null=False, blank=False)
    DOB = models.DateField(null=True, blank=True)
    firstTime = models.TimeField(null=True, blank=False)
    firstDate = models.DateField(null=True, blank=True) # date may be unknown
    firstLocation = models.TextField(null=True, blank=True)
    IndoorTime = models.TimeField(null=True, blank=True)
    IndoorDate = models.DateField(null=True, blank=True) # date may be unknown
    IndoorLocation = models.TextField(null=True, blank=True)
    OutdoorTime = models.TimeField(null=True, blank=True)
    OutdoorDate = models.DateField(null=True, blank=True) # date may be unknown
    OutdoorLocation = models.TextField(null=True, blank=True)
    countries = models.CharField(max_length = 100, blank=True, null=True)
    bestTime = models.TimeField(null=True, blank=True)
    bestDate = models.DateField(null=True, blank=True)
    
    
    def __str__(self):
        """
        The users name (first + last) is the returned string.
        """
        if self.bestTime:
            description = self.name + ' best sub 4: ' + self.bestTime.strftime("%M:%S.%f")
        else:
            description = self.name
        return description
