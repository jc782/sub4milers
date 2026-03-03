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


class Athlete(models.Model):
    """
    Represents a unique athlete who has run sub 4.
    """
    name = models.CharField(max_length=1000, null=False, blank=False)
    DOB = models.DateField(null=True, blank=True)
    countries = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.name

    def best_time(self):
        perf = self.performances.order_by('time').first()
        return perf.time if perf else None

    def first_sub4(self):
        return self.performances.order_by('date').first()
    
    def save(self, *args, **kwargs):
        if self.countries:
            # Normalise each country code to uppercase, strip whitespace
            codes = [c.strip().upper() for c in self.countries.replace('/', ',').split(',')]
            self.countries = ','.join(codes)
        super().save(*args, **kwargs)


class Performance(models.Model):
    """
    A single sub-4 mile performance by an athlete.
    """
    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name='performances'
    )
    time = models.TimeField(null=False, blank=False)
    date = models.DateField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    is_first_sub4 = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['time']

    def __str__(self):
        return f"{self.athlete.name} — {self.time.strftime('%M:%S.%f')} ({self.date})"