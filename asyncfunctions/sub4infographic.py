import django, os, logging
from decouple import config
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd
import seaborn as sns
import calendar
import geopandas as gpd
import plotly.graph_objs as go


matplotlib.use('TkAgg')  # Use the TkAgg backend (you can replace 'TkAgg' with an appropriate backend)


# python manage.py shell < asyncfunctions/sub4infographic.py

def startDjango():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sub4milers.settings")
    django.setup()
    

startDjango()
from sub4.models import AthleteSub4 
athletes = AthleteSub4.objects.all()

names = []
times = []
dates = []
besttimes = []
countries = []
for athlete in athletes:
    names.append(athlete.name)
    times.append(athlete.firstTime)
    dates.append(athlete.firstDate)
    besttimes.append(athlete.bestTime)
    countries.append(athlete.countries)

result_list = [item.strip("'[]'") for item in countries]

data = {
    'Date': dates,
    'Time': times,
    'BestTime': besttimes,
    'Country': countries,
}

df = pd.DataFrame(data)

## Heatmap of the best month to be running sub 4
df['Date'] = pd.to_datetime(df['Date'])
df['Year'] = df['Date'].dt.year
df['Month'] = df['Date'].dt.month

# Create a pivot table to count the number of sub-4 mile runs in each month and year
heatmap_data = df.pivot_table(values='Date', index='Month', columns='Year', aggfunc='count', fill_value=0)

cmap = sns.color_palette("magma")
mask = heatmap_data == 0
vmin = 0  # Minimum count value
vmax = heatmap_data.values.max()  # Maximum count value
norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

# Create the heatmap
plt.figure(figsize=(10, 6))
sns.heatmap(heatmap_data, annot=False, fmt="", mask=mask, cmap='viridis', norm=norm)

# Customize the plot
month_names = [calendar.month_name[i] for i in range(1, 13)]
plt.yticks([i + 0.5 for i in range(12)], month_names, rotation=0, fontsize=14)
plt.xticks(rotation=90, fontsize=14)
for i, label in enumerate(plt.gca().xaxis.get_ticklabels()):
    if i % 3 != 0:
        label.set_visible(False)
plt.xlabel('Year', fontsize=16)
plt.ylabel('Month', fontsize=16)
plt.title('Heatmap of Sub-4 Mile Runs by Month and Year', fontsize = 20)
plt.subplots_adjust(bottom=0.2)
plt.annotate('* data up until 22nd July 2023', xy=(0.5, -0.3), xycoords='axes fraction', fontsize=10, ha='center')


## Start another plot of the histogram of all times run
best_times_seconds = [(time.hour * 3600 + time.minute * 60 + time.second + 1e-6 * time.microsecond) for time in besttimes]

# Define the bin edges from 239 seconds (3 minutes and 59 seconds) down to 0 seconds in one-second increments
bin_edges = list(range(220, 240, 1))

# Create the histogram
plt.figure(figsize=(10, 6))
plt.hist(best_times_seconds, bins=bin_edges, edgecolor='black', alpha=0.7)

# Customize the plot
plt.xlabel('Time (seconds)')
plt.ylabel('Frequency')
plt.title('Histogram of Best Times (3:59 and Below)')
plt.grid(True)
bin_labels = [f"{3}:{59-i:02d}" for i in bin_edges]
plt.xticks(bin_edges, bin_labels, rotation=45)


### Start another plot for the world countries
world = gpd.read_file(gpd.datasets.get_path('naturalearth_lowres'))


df['Country'] = df['Country'].str.upper()
df['Country'] = df['Country'].mask((df['Country']== "GER"), "DEU")


df2 = df.groupby(['Country'])['Country'].value_counts()
print(df2.head(10))

grouped = df.groupby('Country').size()
grouped_df = grouped.to_frame(name = 'Count').reset_index()
grouped_df['log_sub_4_milers'] = np.log10(grouped_df['Count'])

cmap = plt.get_cmap('magma')  # You can choose another colormap if desired

world = world.merge(grouped_df, left_on='iso_a3', right_on='Country', how='left')
world['milersPP'] = world['Count'] / world['pop_est']


# Create the map with logarithmic scale for colors
fig, ax = plt.subplots(1, figsize=(15, 10))
ax.set_title('Number of Sub 4 Milers in each Country')
world.boundary.plot(ax=ax, linewidth=0.6, color='k')
world.plot(column='Count', norm = matplotlib.colors.LogNorm(1, 1000), cmap=cmap, ax=ax, legend=True, legend_kwds={'label': "Number of Sub 4 milers"})
ax.axis('off')

fig, ax = plt.subplots(1, figsize=(15, 10))
ax.set_title('Number of Sub 4 Milers per Capita')
world.boundary.plot(ax=ax, linewidth=0.6, color='k')
world.plot(column='milersPP', cmap=cmap, ax=ax, legend=True, legend_kwds={'label': "Number of Sub 4 milers per capita"})
ax.axis('off')

plt.show()

