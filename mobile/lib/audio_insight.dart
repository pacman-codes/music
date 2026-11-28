import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';
import 'player.dart';

/// Descriptors computed from the actual local audio, not catalog genre guesses.
class AudioInsightPage extends StatefulWidget {
  final LainApi api;
  final LainPlayer player;
  final Track track;
  const AudioInsightPage({super.key,required this.api,required this.player,required this.track});
  @override State<AudioInsightPage> createState()=>_AudioInsightPageState();
}

class _AudioInsightPageState extends State<AudioInsightPage>{
  Map<String,dynamic>? data;
  List<Track> related=[];
  bool loading=false;
  String? error;
  int _revision=0;
  @override void initState(){super.initState();unawaited(load());}
  Future<void> load({bool prioritize=false})async{
    final revision=++_revision;
    setState((){loading=true;error=null;});
    try{
      if(prioritize){
        await widget.api.request('POST','/v1/media/prepare',body:{'track_ids':[widget.track.id]});
      }
      final values=Map<String,dynamic>.from(await widget.api.request('GET','/v1/tracks/${widget.track.id}/analysis') as Map);
      final matches=await widget.api.request('GET','/v1/tracks/${widget.track.id}/similar') as Map;
      if(mounted&&revision==_revision)setState((){data=values;related=widget.api.rows(matches['results']);});
    }catch(e){if(mounted&&revision==_revision)setState(()=>error=e.toString());}
    finally{if(mounted&&revision==_revision)setState(()=>loading=false);}
  }
  String state(dynamic raw)=>switch(raw){'ready'=>'Готово','failed'=>'Не удалось обработать','unavailable'=>'Нет оригинала',_=>'Ожидает обработки'};
  Widget bar(String title,num value)=>Padding(padding:const EdgeInsets.symmetric(vertical:6),child:Column(
    crossAxisAlignment:CrossAxisAlignment.start,children:[
      Text(title),const SizedBox(height:4),LinearProgressIndicator(value:value.clamp(0,1).toDouble()),
    ]));
  @override Widget build(BuildContext context){
    final features=data?['analysis'] as Map?;
    return Scaffold(appBar:AppBar(title:const Text('Звучание'),actions:[
      IconButton(onPressed:loading?null:()=>load(),icon:const Icon(Icons.refresh)),
    ]),body:ListView(padding:const EdgeInsets.all(16),children:[
      Text(widget.track.title,style:Theme.of(context).textTheme.headlineSmall),
      Text(widget.track.artist),
      if(loading)const LinearProgressIndicator(),
      if(error!=null)Text(error!,style:TextStyle(color:Theme.of(context).colorScheme.error)),
      if(data!=null)...[
        ListTile(contentPadding:EdgeInsets.zero,title:const Text('Анализ сигнала'),subtitle:Text(state(data!['analysis_state']))),
        ListTile(contentPadding:EdgeInsets.zero,title:const Text('Экономичная версия'),
          subtitle:Text('${state(data!['economy_state'])}${data!['economy_size'] is int?' · ${((data!['economy_size'] as int)/1048576).toStringAsFixed(2)} МБ':''}')),
        if(data!['analysis_state']=='pending'||data!['economy_state']=='pending')
          OutlinedButton(onPressed:loading?null:()=>load(prioritize:true),child:const Text('Подготовить на сервере')),
      ],
      if(features!=null)...[
        Text('Баланс частот',style:Theme.of(context).textTheme.titleLarge),
        bar('Низкие',features['low'] as num),bar('Средние',features['mid'] as num),bar('Высокие',features['high'] as num),
        const SizedBox(height:10),
        ListTile(contentPadding:EdgeInsets.zero,title:const Text('Приблизительный темп'),
          subtitle:Text(features['tempo_estimate']==null?'Уверенная периодичность не найдена':'≈ ${features['tempo_estimate']} BPM')),
        ListTile(contentPadding:EdgeInsets.zero,title:const Text('Динамика фрагмента'),
          subtitle:Text('${features['dynamic_db']} дБ')),
        ListTile(contentPadding:EdgeInsets.zero,title:const Text('Аудиоформат оригинала'),
          subtitle:Text('${features['codec']} · ${features['sample_rate']} Гц · ${features['channels']} кан.')),
        Text('Измерено по фрагменту длительностью ${features['sample_seconds']} с. Баланс частот приблизительный; BPM может определяться в половинном или двойном темпе. Это не жанровая классификация и не нейросеть.',
          style:Theme.of(context).textTheme.bodySmall),
        const Divider(),Text('Похожие по звучанию',style:Theme.of(context).textTheme.titleLarge),
        const Text('Только проанализированные аудиофайлы твоей медиатеки.'),
        if(related.isEmpty)const Text('Пока нет подходящих проанализированных записей.'),
        if(related.isNotEmpty)FilledButton.icon(onPressed:()=>widget.player.start(related),
          icon:const Icon(Icons.play_arrow),label:const Text('Слушать подборку')),
        for(var i=0;i<related.length;i++)ListTile(leading:const Icon(Icons.music_note),title:Text(related[i].title),
          subtitle:Text(related[i].artist),onTap:()=>widget.player.start(related,initial:i)),
      ],
    ]));
  }
}
